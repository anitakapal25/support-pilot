"""Exact pgvector + PostgreSQL full-text ranking, fused with RRF (not confidence)."""

import re
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.embeddings import make_embedder
from app.models import ArticleVersion, KnowledgeChunk, Message
from app.schemas import Citation
from app.telemetry import emit

# Generic request wording contributes little evidence of the subject being answered.
FILLER = {
    "please",
    "find",
    "tell",
    "help",
    "policy",
    "policies",
    "get",
    "information",
    "need",
    "needed",
    "should",
    "would",
    "could",
}
CLARIFY = "I couldn't find enough current policy evidence to answer that. Please clarify the shipping, return, damaged-item, or account question. No support transfer has been created."


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores = defaultdict(float)
    for ranking in rankings:
        for position, item in enumerate(ranking, 1):
            scores[item] += 1 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class Retriever:
    def __init__(self, engine, settings, embedder=None):
        self.engine, self.settings = engine, settings
        self.embedder = embedder or make_embedder(settings)

    def search(self, query: str, timeout: float = 6) -> list[dict]:
        query = re.sub(r"\bSP-\d+\b", "", query, flags=re.IGNORECASE)
        # Normalize ordinary duration wording and parcel/package synonyms without
        # weakening the coverage threshold or stripping substantive qualifiers.
        query = re.sub(r"\bparcel\b", "package", query, flags=re.IGNORECASE)
        if re.search(r"\bhow long\b", query, re.IGNORECASE):
            query = re.sub(r"\bhow long\b", "time", query, flags=re.IGNORECASE)
            query = re.sub(r"\btake\b", "", query, flags=re.IGNORECASE)
        vector = self.embedder.embed([query], timeout)[0]
        with Session(self.engine) as db:
            # One statement gives both rankings the same MVCC snapshot. Only compatible,
            # active English versions are eligible for either branch.
            rows = (
                db.execute(
                    text("""
                WITH eligible AS MATERIALIZED (
                    SELECT c.id, c.position, c.section, c.content, c.search, c.embedding,
                           v.id AS version_id, v.article_id, v.title, v.updated_at, v.source
                    FROM knowledge_chunks c JOIN article_versions v ON v.id=c.version_id
                    WHERE v.active AND v.locale='en' AND v.embedding_identity=:identity
                ), query AS (
                    SELECT tsvector_to_array(to_tsvector('english', :query)) AS terms
                ), ranked AS (
                    SELECT *, row_number() OVER (ORDER BY embedding <=> CAST(:vector AS vector), article_id, position) AS dense_rank,
                        row_number() OVER (ORDER BY ts_rank_cd(search, to_tsquery('english',
                            COALESCE(NULLIF(array_to_string((SELECT terms FROM query), ' | '), ''), 'zzemptyzz'))) DESC, article_id, position) AS lexical_rank,
                        ts_rank_cd(search, to_tsquery('english',
                            COALESCE(NULLIF(array_to_string((SELECT terms FROM query), ' | '), ''), 'zzemptyzz'))) AS lexical_score,
                        tsvector_to_array(search) AS document_terms,
                        (SELECT terms FROM query) AS query_terms
                    FROM eligible
                ) SELECT * FROM ranked WHERE dense_rank <= 20 OR (lexical_rank <= 20 AND lexical_score > 0)
            """),
                    {
                        "identity": self.embedder.identity,
                        "query": " ".join(
                            word
                            for word in re.findall(r"[\w]+", query.lower())
                            if word not in FILLER
                        ),
                        "vector": str(vector),
                    },
                )
                .mappings()
                .all()
            )
        records = {str(row["id"]): dict(row) for row in rows}
        dense = [
            str(row["id"])
            for row in sorted(rows, key=lambda row: row["dense_rank"])
            if row["dense_rank"] <= 20
        ]
        lexical = [
            str(row["id"])
            for row in sorted(rows, key=lambda row: row["lexical_rank"])
            if row["lexical_rank"] <= 20 and row["lexical_score"] > 0
        ]
        candidates = []
        # UUIDs change on ingestion; break rank-fusion ties using stable source positions.
        fused = sorted(
            rrf([dense, lexical]),
            key=lambda item: (
                -item[1],
                records[item[0]]["article_id"],
                records[item[0]]["position"],
            ),
        )
        for id, score in fused[:5]:
            row = records[id]
            terms = set(row["query_terms"])
            matched = terms & set(row["document_terms"])
            row.update(
                id=id,
                version_id=str(row["version_id"]),
                rrf=score,
                coverage=len(matched) / max(1, len(terms)),
                matched=len(matched),
            )
            candidates.append(row)
        emit(
            "retrieval.result", source_ids=[row["id"] for row in candidates], count=len(candidates)
        )
        return candidates

    def answer(self, query: str, timeout: float = 6) -> dict:
        candidates = self.search(query, timeout)
        supported = [
            row
            for row in candidates
            if row["coverage"] >= self.settings.evidence_coverage and row["matched"] >= 2
        ]
        if not supported:
            return {"policy_answer": CLARIFY, "citations": []}
        # Extractive answer: every policy sentence is the exact retrieved section.
        # Return at most two quotations; top five remain retrieval candidates for evals.
        selected = []
        for row in supported:
            if row["article_id"] not in {item["article_id"] for item in selected}:
                selected.append(row)
            if len(selected) == 2:
                break
        citations = [
            Citation(
                id=row["id"],
                article_id=row["article_id"],
                version_id=row["version_id"],
                title=row["title"],
                section=row["section"],
                updated_at=row["updated_at"],
                excerpt=row["content"],
            ).model_dump(mode="json")
            for row in selected
        ]
        return {
            "policy_answer": "\n\n".join(
                f"[{index}] {item['title']} — {item['section']}\n{item['excerpt']}"
                for index, item in enumerate(citations, 1)
            ),
            "citations": citations,
        }


def validate_current_citations(conn, citations: list[dict]) -> bool:
    """Run inside final publication transaction; block activation until publication commits."""
    if not citations:
        return True
    rows = conn.execute(
        """SELECT c.id,c.content,c.section,v.id AS version_id,v.title,v.article_id
        FROM knowledge_chunks c JOIN article_versions v ON v.id=c.version_id
        WHERE c.id=ANY(%s::uuid[]) AND v.active FOR SHARE OF v""",
        ([item["id"] for item in citations],),
    ).fetchall()
    found = {str(row["id"]): row for row in rows}
    return all(
        item["id"] in found
        and all(
            str(found[item["id"]][field]) == item[target]
            for field, target in [
                ("content", "excerpt"),
                ("section", "section"),
                ("version_id", "version_id"),
                ("title", "title"),
                ("article_id", "article_id"),
            ]
        )
        for item in citations
    )


def citation_source(engine, customer, conversation_id: str, chunk_id: str) -> dict:
    from app.conversations import authorize

    with Session(engine) as db:
        authorize(db, customer, conversation_id)
        results = db.scalars(
            select(Message.result).where(
                Message.conversation_id == conversation_id, Message.role == "assistant"
            )
        ).all()
        if not any(
            item.get("id") == chunk_id
            for result in results
            if result
            for item in result.get("citations", [])
        ):
            raise HTTPException(404, "Source unavailable")
        row = db.execute(
            select(KnowledgeChunk, ArticleVersion)
            .join(ArticleVersion, ArticleVersion.id == KnowledgeChunk.version_id)
            .where(KnowledgeChunk.id == chunk_id)
        ).first()
        if not row:
            raise HTTPException(404, "Source unavailable")
        chunk, version = row
        return {
            "id": chunk.id,
            "title": version.title,
            "section": chunk.section,
            "content": chunk.content,
            "version_id": version.id,
            "updated_at": version.updated_at,
            "active": version.active,
        }
