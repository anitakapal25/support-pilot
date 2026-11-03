from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.config import Settings
from app.maintenance import cleanup, set_pilot
from app.models import (
    AgentRun,
    Conversation,
    OperationalCounter,
    PilotControl,
    UsageReservation,
    utcnow,
)
from app.operations import SpendLimitExceeded, admission, reserve_spend
from conftest import create_chat, sign_in
from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session


@pytest.fixture
def ops_engine(db_engine):
    with Session(db_engine) as db, db.begin():
        for model in (OperationalCounter, PilotControl, UsageReservation):
            db.execute(delete(model))
    yield db_engine
    with Session(db_engine) as db, db.begin():
        for model in (OperationalCounter, PilotControl, UsageReservation):
            db.execute(delete(model))


def test_spend_reservations_serialize_across_workers(ops_engine):
    settings = Settings(
        _env_file=None,
        spend_limits_enabled=True,
        monthly_model_budget_usd=0.01,
        model_input_usd_per_million=1,
        model_output_usd_per_million=1,
    )

    def reserve(_):
        try:
            reserve_spend(ops_engine, settings, "openai", 2000)
            return True
        except SpendLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(12))) == 5
    with Session(ops_engine) as db:
        assert sum(db.scalars(select(UsageReservation.reserved_micro_usd))) == 10000
    with pytest.raises(SpendLimitExceeded):
        reserve_spend(
            ops_engine,
            settings.model_copy(update={"model_input_usd_per_million": None}),
            "openai",
            1,
        )


def test_shared_kill_switch_and_rate_limits(ops_engine):
    settings = Settings(_env_file=None, request_limits_enabled=True, requests_per_minute=2)
    actor = SimpleNamespace(id=str(uuid4()))
    admission(ops_engine, settings, actor)
    admission(ops_engine, settings, actor)
    with pytest.raises(HTTPException) as error:
        admission(ops_engine, settings, actor)
    assert error.value.status_code == 429
    set_pilot(ops_engine, False, 100)
    with pytest.raises(HTTPException) as error:
        admission(ops_engine, settings, actor, automation=True)
    assert error.value.status_code == 503
    set_pilot(ops_engine, True, 0)
    with pytest.raises(HTTPException):
        admission(ops_engine, settings, actor, automation=True)


def test_pause_preserves_human_support(client, ops_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    set_pilot(ops_engine, False, 100)
    response = client.post(
        f"/api/conversations/{conversation}/messages",
        headers=headers,
        json={"request_id": str(uuid4()), "message": "SP-1001"},
    )
    assert response.status_code == 503
    queued = client.post(
        f"/api/conversations/{conversation}/handoff",
        headers=headers,
        json={"reason": "customer_request", "summary": "Need human help"},
    )
    assert queued.status_code == 200 and queued.json()["status"] == "queued"


def test_retention_removes_matching_checkpoints_only(client, ops_engine):
    headers = sign_in(client)
    old = create_chat(client, headers)
    recent = create_chat(client, headers)
    for id in (old, recent):
        assert (
            client.post(
                f"/api/conversations/{id}/messages",
                headers=headers,
                json={"request_id": str(uuid4()), "message": "SP-1001"},
            ).status_code
            == 200
        )
    with Session(ops_engine) as db:
        old_run = db.scalar(select(AgentRun.id).where(AgentRun.conversation_id == old))
        recent_run = db.scalar(select(AgentRun.id).where(AgentRun.conversation_id == recent))
    cutoff = utcnow() - timedelta(days=40)
    with Session(ops_engine) as db, db.begin():
        db.execute(update(Conversation).where(Conversation.id == old).values(created_at=cutoff))
        db.execute(
            update(AgentRun)
            .where(AgentRun.conversation_id == old)
            .values(created_at=cutoff, updated_at=cutoff)
        )
        db.execute(
            text("UPDATE messages SET created_at=:time WHERE conversation_id=:id"),
            {"time": cutoff, "id": old},
        )
    assert cleanup(ops_engine, dry_run=True)["eligible_conversations"] == 1
    assert cleanup(ops_engine, dry_run=False)["deleted_conversations"] == 1
    with Session(ops_engine) as db:
        assert db.get(Conversation, old) is None and db.get(Conversation, recent) is not None
        assert (
            db.scalar(text("SELECT count(*) FROM checkpoints WHERE thread_id=:id"), {"id": old_run})
            == 0
        )
        assert (
            db.scalar(
                text("SELECT count(*) FROM checkpoints WHERE thread_id=:id"), {"id": recent_run}
            )
            > 0
        )


def test_render_url_uses_psycopg_without_exposing_password():
    settings = Settings(_env_file=None, database_url="postgres://user:secret@db/support")
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert "secret" not in repr(settings)
