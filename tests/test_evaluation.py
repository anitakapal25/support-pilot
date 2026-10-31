import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from app.config import Settings
from app.eval_dataset import load_dataset
from app.evaluate_release import digest, percentile
from app.models import AuditEvent, RequestTrace
from app.provider import OpenAIProvider, ProviderUnavailable
from app.review_release import calibrate, validate_review
from app.telemetry import CURRENT, TraceBuffer, audit_session, emit, record_usage
from conftest import create_chat, sign_in
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_dataset_split_and_leakage(tmp_path):
    path = Path("evals/scenarios.json")
    cases, fingerprint, _ = load_dataset(path)
    assert len(cases) == 50 and len(fingerprint) == 64
    assert sum(case.split == "heldout" for case in cases) == 15
    bad = json.loads(path.read_text())
    bad["cases"][-1]["paraphrase_group"] = bad["cases"][0]["paraphrase_group"]
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="crosses"):
        load_dataset(target)


def test_telemetry_allowlist_and_bounded_storage():
    trace = TraceBuffer()
    token = CURRENT.set(trace)
    try:
        emit(
            "agent.route",
            action="lookup_order",
            prompt="SECRET",
            password="SECRET",
            outcome="SECRET",
            source_ids=["SECRET", str(uuid4())],
        )
        emit("SECRET", action="lookup_order")
        for _ in range(110):
            emit("provider.failure", outcome="unavailable")
        assert len(trace.events) == 96 and trace.dropped_events == 15
        assert "SECRET" not in json.dumps(trace.events)
    finally:
        CURRENT.reset(token)


def test_usage_cost_unknown_and_cached_tokens():
    trace = TraceBuffer()
    token = CURRENT.set(trace)
    settings = Settings(
        _env_file=None,
        model_input_usd_per_million=2,
        model_cached_input_usd_per_million=1,
        model_output_usd_per_million=4,
    )
    try:
        record_usage("openai", None, settings)
        assert trace.events[-1]["usage_known"] is False
        assert trace.events[-1]["cost_usd"] is None
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            input_tokens_details=SimpleNamespace(cached_tokens=200),
        )
        record_usage("openai", usage, settings)
        assert trace.events[-1]["cost_usd"] == pytest.approx(0.0022)
        usage.input_tokens_details.cached_tokens = 2000
        record_usage("openai", usage, settings)
        assert trace.events[-1]["usage_known"] is False
    finally:
        CURRENT.reset(token)


def test_input_budget_prevents_provider_request():
    settings = Settings(
        _env_file=None,
        openai_api_key="test-only",
        openai_model="test-model",
        model_input_token_limit=256,
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    with patch("app.provider.AsyncOpenAI", return_value=client), pytest.raises(ProviderUnavailable):
        OpenAIProvider(settings).decide("hello " * 2000, 6)
    client.responses.create.assert_not_called()


def test_request_trace_redacts_text_headers_and_query(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    secret = "unique-secret-sentinel"
    response = client.post(
        f"/api/conversations/{conversation}/messages?secret={secret}",
        headers={**headers, "X-Request-ID": secret},
        json={"request_id": str(uuid4()), "message": secret + " SP-1001"},
    )
    assert response.status_code == 200
    UUID(response.headers["X-Request-ID"])
    with Session(db_engine) as db:
        row = db.get(RequestTrace, response.headers["X-Request-ID"])
        assert row.conversation_id == conversation
        assert row.route == "/api/conversations/{conversation_id}/messages"
        serialized = json.dumps(row.events)
        assert secret not in serialized and "SP-1001" not in serialized
        assert any(event["event"] == "agent.completed" for event in row.events)
        assert row.actor_id is not None


def test_audit_rolls_back_with_business_transaction(db_engine):
    resource = str(uuid4())
    with Session(db_engine) as db:
        audit_session(db, str(uuid4()), "ticket.created", resource)
        db.flush()
        db.rollback()
    with Session(db_engine) as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.resource_id == resource)) is None


def test_review_cannot_accept_missing_labels_or_changed_evidence():
    evidence = {
        "id": "synthetic",
        "expected": {},
        "inputs": {},
        "messages": [],
        "sources": [],
        "order_evidence": [],
    }
    original = {
        "run_id": "test",
        "dataset_sha256": "test",
        "cases": [
            {**evidence, "evidence_sha256": digest(evidence), "grounded": None, "resolved": None}
        ],
    }
    reviewed = copy.deepcopy(original)
    reviewed.update(
        reviewer="Synthetic test fixture", reviewed_at="2026-10-31", attested_human_review=True
    )
    with pytest.raises(ValueError, match="boolean"):
        validate_review(original, reviewed)
    reviewed["cases"][0].update(grounded=True, resolved=True)
    assert validate_review(original, reviewed)["passed"]
    reviewed["cases"][0]["messages"] = ["altered"]
    with pytest.raises(ValueError, match="evidence"):
        validate_review(original, reviewed)


def test_judge_calibration_requires_negative_examples():
    human = {
        "run_id": "synthetic",
        "dataset_sha256": "test",
        "cases": [{"id": str(i), "grounded": True, "resolved": True} for i in range(20)],
    }
    result = calibrate(human, copy.deepcopy(human))
    assert result["advisory_only"] and not result["grounded"]["calibrated"]
    for row in human["cases"][:5]:
        row.update(grounded=False, resolved=False)
    assert calibrate(human, copy.deepcopy(human))["grounded"]["calibrated"]
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
