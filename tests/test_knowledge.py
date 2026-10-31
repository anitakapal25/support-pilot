import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import tiktoken
from app.config import Settings
from app.embeddings import DemoEmbedder, OpenAIEmbedder, validate_vectors
from app.ingest import chunk_markdown, ingest
from app.models import Article, ArticleVersion, KnowledgeChunk
from app.provider import ProviderUnavailable
from app.retrieval import Retriever, rrf
from conftest import create_chat, sign_in
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from test_conversations import new_conversation, service


def configuration(**kwargs):
    return Settings(_env_file=None, agent_provider="demo", **kwargs)


@pytest.fixture
def knowledge_db(db_engine):
    with Session(db_engine) as db, db.begin():
        db.execute(delete(KnowledgeChunk))
        db.execute(delete(ArticleVersion))
        db.execute(delete(Article))
    ingest(db_engine, Path("knowledge"), DemoEmbedder(), configuration())
    return db_engine


def test_chunking_preserves_hierarchy_tables_and_token_limits():
    body = (
        "# Root\n## Child\n\n"
        + "A sentence about parcels. " * 200
        + "\n\n| Name | Cost |\n| --- | --- |\n"
        + "\n".join(f"| Parcel {i} | $5 |" for i in range(50))
    )
    chunks = chunk_markdown(body, 100, 10)
    encoding = tiktoken.get_encoding("cl100k_base")
    assert len(chunks) > 5
    assert all(section == "Root > Child" for section, content in chunks)
    assert all(len(encoding.encode(section + "\n" + content)) <= 100 for section, content in chunks)
    assert all(
        content.startswith("| Name | Cost |\n| --- | --- |")
        for section, content in chunks
        if "| Parcel" in content
    )
    with pytest.raises(ValueError):
        chunk_markdown("# Heading only", 100, 10)


def test_rrf_merges_ranks_not_scores():
    rows = rrf([["a", "b"], ["b", "c"]])
    assert rows[0][0] == "b"
    assert rows[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_embedding_contract_fails_closed():
    for vectors in ([[0.0, 0.0]], [[float("nan"), 1]], [[1.0]], []):
        with pytest.raises(ProviderUnavailable):
            validate_vectors(vectors, 1, 2)
    with patch("app.embeddings.AsyncOpenAI") as sdk:
        client = MagicMock()
        client.embeddings.create = AsyncMock(
            return_value=SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0] * 64)])
        )
        sdk.return_value.__aenter__.return_value = client
        provider = OpenAIEmbedder(
            configuration(embedding_dimensions=64, openai_api_key="fake-test-key")
        )
        assert len(provider.embed(["test"])[0]) == 64
        assert sdk.call_args.kwargs["max_retries"] == 0
        assert client.embeddings.create.call_args.kwargs["dimensions"] == 64
        client.embeddings.create.return_value.data[0].index = 1
        with pytest.raises(ProviderUnavailable):
            provider.embed(["test"])


@pytest.mark.integration
def test_ingestion_idempotency_updates_deletions_and_failed_staging(knowledge_db, tmp_path):
    engine = knowledge_db
    shutil.copytree("knowledge", tmp_path / "corpus")
    corpus = tmp_path / "corpus"
    with Session(engine) as db:
        original = db.scalar(select(func.count()).select_from(KnowledgeChunk))
    ingest(engine, corpus, DemoEmbedder(), configuration())
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeChunk)) == original
    file = corpus / "return-window.md"
    file.write_text(file.read_text().replace("30 calendar days", "21 calendar days"))
    (corpus / "guest-orders.md").unlink()
    ingest(engine, corpus, DemoEmbedder(), configuration())
    with Session(engine) as db:
        versions = db.scalars(
            select(ArticleVersion).where(ArticleVersion.article_id == "return-window")
        ).all()
        assert len(versions) == 2 and sum(v.active for v in versions) == 1
        assert not db.scalar(
            select(ArticleVersion.active).where(ArticleVersion.article_id == "guest-orders")
        )
    rows = Retriever(engine, configuration()).search("return window")
    assert any("21 calendar days" in row["content"] for row in rows)
    assert all("30 calendar days" not in row["content"] for row in rows)
    file.write_text(file.read_text().replace("21 calendar days", "14 calendar days"))
    with (
        patch.object(DemoEmbedder, "embed", side_effect=ProviderUnavailable()),
        pytest.raises(ProviderUnavailable),
    ):
        ingest(engine, corpus, DemoEmbedder(), configuration())
    assert any(
        "21 calendar days" in row["content"]
        for row in Retriever(engine, configuration()).search("return window")
    )


@pytest.mark.integration
def test_policy_citations_are_authorized_and_historical(client, knowledge_db, tmp_path):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    path = f"/api/conversations/{conversation}"
    payload = {"request_id": str(uuid4()), "message": "What is the return window?"}
    reply = client.post(path + "/messages", headers=headers, json=payload)
    assert reply.status_code == 200, reply.text
    response = reply.json()["response"]
    assert response["citations"] and "30 calendar days" in response["message"]
    citation = response["citations"][0]
    source = path + "/sources/" + citation["id"]
    assert client.get(source).json()["content"] == citation["excerpt"]
    assert client.get(path + "/sources/" + str(uuid4())).status_code == 404
    shutil.copytree("knowledge", tmp_path / "corpus")
    file = tmp_path / "corpus" / "return-window.md"
    file.write_text(file.read_text().replace("30 calendar days", "21 calendar days"))
    ingest(knowledge_db, tmp_path / "corpus", DemoEmbedder(), configuration())
    historical = client.get(source).json()
    assert not historical["active"] and "30 calendar days" in historical["content"]
    assert client.post(path + "/messages", headers=headers, json=payload).json() == reply.json()
    sign_in(client, "bob@example.test")
    assert client.get(source).status_code == 404


@pytest.mark.integration
def test_combined_order_and_policy_and_insufficient_evidence(client, knowledge_db):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    path = f"/api/conversations/{conversation}/messages"
    response = client.post(
        path, headers=headers, json={"request_id": str(uuid4()), "message": "SP-1001 return window"}
    ).json()["response"]
    assert response["order"]["order_reference"] == "SP-1001"
    assert response["citations"]
    assert "30 calendar days" in response["message"]
    response = client.post(
        path,
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "message": "What is the return policy for a 90 day trial membership?",
        },
    ).json()["response"]
    assert response["citations"] == [] and response["outcome"] == "clarification"
    assert "No support transfer has been created" in response["message"]


@pytest.mark.integration
def test_superseded_checkpoint_evidence_is_not_published(knowledge_db, tmp_path):
    class Crash(BaseException):
        pass

    conversation = new_conversation(knowledge_db)
    request_id = str(uuid4())
    turn = service(knowledge_db)
    with patch.object(turn, "finalize", side_effect=Crash()), pytest.raises(Crash):
        turn.submit(conversation, request_id, "What is the return window?")
    shutil.copytree("knowledge", tmp_path / "corpus")
    file = tmp_path / "corpus" / "return-window.md"
    file.write_text(file.read_text().replace("30 calendar days", "21 calendar days"))
    ingest(knowledge_db, tmp_path / "corpus", DemoEmbedder(), configuration())
    result = service(knowledge_db).submit(conversation, request_id, "What is the return window?")
    assert result.response.citations == []
    assert result.response.outcome == "clarification"
    assert "30 calendar days" not in result.response.message


@pytest.mark.integration
def test_embedding_identity_mismatch_never_searches_incompatible_vectors(knowledge_db):
    assert (
        Retriever(knowledge_db, configuration(embedding_dimensions=64)).search("return window")
        == []
    )


@pytest.mark.integration
def test_retrieval_regression_gate(knowledge_db):
    from app.evaluate_retrieval import evaluate

    report = evaluate(knowledge_db, configuration(), Path("evals/retrieval.json"))
    assert report["passed"], json.dumps(report, indent=2)


@pytest.mark.integration
def test_forged_excerpt_is_rejected_before_publication(knowledge_db):
    original = Retriever.answer

    def forged(self, query, timeout=6):
        result = original(self, query, timeout)
        result["citations"][0]["excerpt"] = "All refunds are automatically approved."
        result["policy_answer"] = "All refunds are automatically approved."
        return result

    with patch.object(Retriever, "answer", forged):
        result = service(knowledge_db).submit(
            new_conversation(knowledge_db), str(uuid4()), "What is the return window?"
        )
    assert result.response.citations == []
    assert result.response.outcome == "clarification"
    assert "automatically approved" not in result.response.message


@pytest.mark.integration
def test_retrieval_ties_do_not_depend_on_random_chunk_ids(knowledge_db):
    queries = [case["query"] for case in json.loads(Path("evals/retrieval.json").read_text())]
    retriever = Retriever(knowledge_db, configuration())

    def ranking():
        return [
            [(row["article_id"], row["position"]) for row in retriever.search(query)]
            for query in queries
        ]

    before = ranking()
    for _ in range(2):
        with Session(knowledge_db) as db, db.begin():
            for chunk in db.scalars(select(KnowledgeChunk)):
                chunk.id = str(uuid4())
        assert ranking() == before
