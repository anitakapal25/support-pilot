"""Explicit full-corpus ingestion: stage embeddings, then atomically activate a snapshot."""

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import tiktoken
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.embeddings import Embedder, make_embedder, validate_vectors
from app.models import Article, ArticleVersion, KnowledgeChunk


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    locale: str = "en"
    updated_at: datetime


def chunk_markdown(body: str, limit: int, overlap: int) -> list[tuple[str, str]]:
    """Keep heading paths and table headers. Reject oversized indivisible table rows."""
    os.environ.setdefault(
        "TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "tokenizer_cache")
    )
    encoding = tiktoken.get_encoding("cl100k_base")
    count = lambda value: len(encoding.encode(value, disallowed_special=()))
    sections, headings, lines = [], [], []

    def flush():
        if lines:
            sections.append((" > ".join(headings), "\n".join(lines).strip()))
            lines.clear()

    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            flush()
            level = len(match[1])
            headings[:] = headings[: level - 1] + [match[2]]
        else:
            lines.append(line)
    flush()
    chunks = []
    for section, content in sections:
        if not content:
            continue
        budget = limit - count(section) - 4
        if budget < 20:
            raise ValueError("Heading exceeds chunk budget")
        # Tables are row units with repeated header; prose splits on paragraph boundaries.
        for block in re.split(r"\n\s*\n", content):
            block = block.strip()
            if not block:
                continue
            rows = block.splitlines()
            if len(rows) >= 2 and rows[0].startswith("|") and re.match(r"^[| :\-]+$", rows[1]):
                header = "\n".join(rows[:2])
                current = header
                for row in rows[2:]:
                    if count(header + "\n" + row) > budget:
                        raise ValueError("Table row exceeds chunk budget")
                    if count(current + "\n" + row) > budget:
                        chunks.append((section, current))
                        current = header
                    current += "\n" + row
                chunks.append((section, current))
            else:
                tokens = encoding.encode(block, disallowed_special=())
                step = max(1, budget - min(overlap, budget // 3))
                for start in range(0, len(tokens), step):
                    chunk = encoding.decode(tokens[start : start + budget])
                    chunks.append((section, chunk))
                    if start + budget >= len(tokens):
                        break
    if not chunks:
        raise ValueError("Article has no content")
    return chunks


def ingest(engine, directory: Path, embedder: Embedder, settings: Settings) -> dict:
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise ValueError("Refusing empty corpus activation")
    staged, ids = [], set()
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError("Every article requires YAML frontmatter")
        meta = Metadata.model_validate(yaml.safe_load(parts[1]))
        if meta.locale != "en" or meta.updated_at.tzinfo is None or meta.id in ids:
            raise ValueError("Expected unique English articles with timezone-aware dates")
        ids.add(meta.id)
        fingerprint = hashlib.sha256(
            (
                raw
                + json.dumps(
                    [embedder.identity, settings.chunk_tokens, settings.chunk_overlap, "chunk-v1"]
                )
            ).encode()
        ).hexdigest()
        with Session(engine) as db:
            old = db.scalar(
                select(ArticleVersion).where(
                    ArticleVersion.article_id == meta.id, ArticleVersion.fingerprint == fingerprint
                )
            )
            existing_id = old.id if old else None
        chunks = chunk_markdown(parts[2], settings.chunk_tokens, settings.chunk_overlap)
        vectors = []
        if not existing_id:
            for start in range(0, len(chunks), 32):
                batch = chunks[start : start + 32]
                vectors.extend(
                    validate_vectors(
                        embedder.embed([section + "\n" + body for section, body in batch]),
                        len(batch),
                        embedder.dimensions,
                    )
                )
        staged.append((meta, fingerprint, path.name, chunks, vectors))
    # No database changes until every parse/embedding succeeds. Serial activation only.
    with Session(engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(7343003)"))
        db.execute(update(ArticleVersion).values(active=False).where(ArticleVersion.active))
        for meta, fingerprint, source, chunks, vectors in staged:
            db.execute(insert(Article).values(id=meta.id).on_conflict_do_nothing())
            version = db.scalar(
                select(ArticleVersion).where(
                    ArticleVersion.article_id == meta.id, ArticleVersion.fingerprint == fingerprint
                )
            )
            if not version:
                version = ArticleVersion(
                    id=str(uuid4()),
                    article_id=meta.id,
                    fingerprint=fingerprint,
                    title=meta.title,
                    locale=meta.locale,
                    source=source,
                    updated_at=meta.updated_at,
                    embedding_identity=embedder.identity,
                    active=False,
                )
                db.add(version)
                db.flush()
                for position, ((section, content), embedding) in enumerate(
                    zip(chunks, vectors, strict=True)
                ):
                    db.add(
                        KnowledgeChunk(
                            id=str(uuid4()),
                            version_id=version.id,
                            position=position,
                            section=section,
                            content=content,
                            embedding=embedding,
                        )
                    )
            version.active = True
            db.flush()
    return {
        "articles": len(staged),
        "chunks": sum(len(item[3]) for item in staged),
        "embedding": embedder.identity,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Activate a complete synthetic help-center snapshot; missing files are retired."
    )
    parser.add_argument("directory", type=Path, nargs="?", default=Path("knowledge"))
    args = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings)
    try:
        print(json.dumps(ingest(engine, args.directory, make_embedder(settings), settings)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
