import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app.auth import Principal
from app.checkpoints import checkpoint_connection
from app.config import Settings
from app.conversations import DurableTurn, conversation_detail, create_conversation
from app.models import AgentRun, Order, ToolExecution, User
from app.provider import DemoProvider, ProviderUnavailable
from conftest import create_chat, sign_in
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def post(client, headers, conversation, request_id, message="SP-1001"):
    return client.post(
        f"/api/conversations/{conversation}/messages",
        headers=headers,
        json={"request_id": request_id, "message": message},
    )


def principal(engine):
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email == "alice@example.test"))
        return Principal(user.id, user.email, user.name, user.role, "test-csrf")


def service(engine, provider=None, **settings):
    return DurableTurn(
        engine,
        principal(engine),
        provider or DemoProvider(),
        Settings(_env_file=None, agent_provider="demo", **settings),
    )


def new_conversation(engine):
    return create_conversation(engine, principal(engine), str(uuid4())).id


def test_retry_returns_identical_result_and_one_message_pair(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    request_id = str(uuid4())
    first = post(client, headers, conversation, request_id)
    duplicate = post(client, headers, conversation, request_id)
    assert first.status_code == duplicate.status_code == 200
    assert first.json() == duplicate.json()
    detail = client.get(f"/api/conversations/{conversation}").json()
    assert len(detail["messages"]) == 2 and len(detail["runs"]) == 1
    assert [item["sequence"] for item in detail["messages"]] == [1, 2]
    with Session(db_engine) as db:
        run = db.get(AgentRun, first.json()["id"])
        assert run.provider_calls == run.tool_calls == run.attempts == 1
    assert post(client, headers, conversation, request_id, "SP-2001").status_code == 409


def test_conversation_creation_is_idempotent_and_authorized(client):
    headers = sign_in(client)
    request_id = str(uuid4())
    first = client.post("/api/conversations", headers=headers, json={"request_id": request_id})
    second = client.post("/api/conversations", headers=headers, json={"request_id": request_id})
    assert first.json() == second.json()
    assert len(client.get("/api/conversations").json()) == 1
    bob = sign_in(client, "bob@example.test")
    assert (
        client.post("/api/conversations", headers=bob, json={"request_id": request_id}).status_code
        == 404
    )
    assert client.get("/api/conversations").json() == []
    assert client.get(f"/api/conversations/{request_id}").status_code == 404
    assert post(client, bob, request_id, str(uuid4())).status_code == 404
    assert client.get(f"/api/conversations/{request_id}/runs/{uuid4()}").status_code == 404


def test_messages_require_csrf_and_cannot_accept_identity(client):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    assert post(client, {}, conversation, str(uuid4())).status_code == 403
    assert (
        post(
            client, {**headers, "Origin": "https://evil.test"}, conversation, str(uuid4())
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/conversations/{conversation}/messages",
            headers=headers,
            json={"request_id": str(uuid4()), "message": "SP-1001", "customer_id": "bob"},
        ).status_code
        == 422
    )
    assert client.get(f"/api/conversations/{conversation}").json()["messages"] == []


def test_history_survives_new_app_and_followup_rechecks_live_status(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    assert post(client, headers, conversation, str(uuid4())).status_code == 200
    with Session(db_engine) as db, db.begin():
        db.execute(update(Order).where(Order.reference == "SP-1001").values(status="delivered"))
    try:
        # New application instance with a new provider; no process memory reused.
        from app.main import create_app
        from fastapi.testclient import TestClient

        with TestClient(
            create_app(Settings(_env_file=None, agent_provider="demo"), db_engine, DemoProvider())
        ) as restarted:
            fresh_headers = sign_in(restarted)
            assert len(restarted.get(f"/api/conversations/{conversation}").json()["messages"]) == 2
            response = post(
                restarted, fresh_headers, conversation, str(uuid4()), "When will it arrive?"
            )
            assert response.json()["response"]["order"]["status"] == "delivered"
            assert len(restarted.get(f"/api/conversations/{conversation}").json()["messages"]) == 4
    finally:
        with Session(db_engine) as db, db.begin():
            db.execute(update(Order).where(Order.reference == "SP-1001").values(status="shipped"))


def test_order_access_is_rechecked_for_followup(db_engine):
    conversation = new_conversation(db_engine)
    assert service(db_engine).submit(conversation, str(uuid4()), "SP-1001").status == "completed"
    with Session(db_engine) as db:
        bob = db.scalar(select(User.id).where(User.email == "bob@example.test"))
    with Session(db_engine) as db, db.begin():
        db.execute(update(Order).where(Order.reference == "SP-1001").values(customer_id=bob))
    try:
        result = service(db_engine).submit(
            conversation, str(uuid4()), "What is its delivery status?"
        )
        assert result.response.outcome == "unavailable" and result.response.order is None
    finally:
        with Session(db_engine) as db, db.begin():
            db.execute(
                update(Order)
                .where(Order.reference == "SP-1001")
                .values(customer_id=principal(db_engine).id)
            )


def test_no_memory_bleed_between_conversations_or_failed_orders(db_engine):
    first, second = new_conversation(db_engine), new_conversation(db_engine)
    service(db_engine).submit(first, str(uuid4()), "SP-1001")
    assert (
        service(db_engine).submit(second, str(uuid4()), "When will it arrive?").response.outcome
        == "clarification"
    )
    service(db_engine).submit(first, str(uuid4()), "SP-2001")
    assert (
        service(db_engine).submit(first, str(uuid4()), "When will it arrive?").response.outcome
        == "clarification"
    )


def test_recovery_after_graph_completion_does_not_rerun_provider(db_engine):
    conversation, request_id = new_conversation(db_engine), str(uuid4())

    class Crash(BaseException):
        pass

    with patch.object(DurableTurn, "finalize", side_effect=Crash), pytest.raises(Crash):
        service(db_engine).submit(conversation, request_id, "SP-1001")
    fail_if_called = Mock()
    fail_if_called.decide.side_effect = AssertionError("Completed checkpoint must not reroute")
    recovered = service(db_engine, fail_if_called).submit(conversation, request_id, "SP-1001")
    assert recovered.status == "completed"
    assert len(conversation_detail(db_engine, principal(db_engine), conversation).messages) == 2
    fail_if_called.decide.assert_not_called()


def test_actual_process_crash_after_tool_commit_recovers_from_postgres(db_engine):
    conversation, request_id = new_conversation(db_engine), str(uuid4())
    customer = principal(db_engine)
    env = {
        **os.environ,
        "PYTHONPATH": "backend",
        "RECOVERY_DATABASE_URL": db_engine.url.render_as_string(hide_password=False),
    }
    command = [sys.executable, "tests/recovery_worker.py", conversation, request_id, customer.id]
    crashed = subprocess.run(
        command + ["crash"], env=env, capture_output=True, timeout=20, check=False
    )
    assert crashed.returncode == 17, crashed.stderr.decode()
    with Session(db_engine) as db:
        run = db.scalar(select(AgentRun).where(AgentRun.request_id == request_id))
        assert run.status == "running"
        assert db.get(ToolExecution, (run.id, "lookup_order")) is not None
    resumed = subprocess.run(
        command + ["resume"], env=env, capture_output=True, timeout=20, check=False
    )
    assert resumed.returncode == 0, resumed.stderr.decode()
    with Session(db_engine) as db:
        run = db.scalar(select(AgentRun).where(AgentRun.request_id == request_id))
        assert run.status == "completed"
        assert run.provider_calls == run.tool_calls == 1
        assert run.attempts == 2
    assert len(conversation_detail(db_engine, customer, conversation).messages) == 2


def test_same_conversation_serializes_and_other_conversations_progress(db_engine):
    first, second, request_id = (
        new_conversation(db_engine),
        new_conversation(db_engine),
        str(uuid4()),
    )
    started, release = Event(), Event()

    class SlowProvider(DemoProvider):
        def decide(self, message, timeout, context=None):
            started.set()
            assert release.wait(10)
            return super().decide(message, timeout, context)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(service(db_engine, SlowProvider()).submit, first, request_id, "SP-1001")
        try:
            assert started.wait(5)
            duplicate = service(db_engine).submit(first, request_id, "SP-1001")
            assert duplicate.status == "running"
            with pytest.raises(HTTPException) as busy:
                service(db_engine).submit(first, str(uuid4()), "SP-1001")
            assert busy.value.status_code == 409
            assert service(db_engine).submit(second, str(uuid4()), "SP-1001").status == "completed"
        finally:
            release.set()
        assert task.result(timeout=5).status == "completed"
    assert len(conversation_detail(db_engine, principal(db_engine), first).messages) == 2


def test_five_conversations_do_not_exhaust_application_pool(db_engine):
    conversations = [new_conversation(db_engine) for _ in range(5)]
    barrier = Barrier(5)

    class ConcurrentProvider(DemoProvider):
        def decide(self, message, timeout, context=None):
            barrier.wait(timeout=5)
            return super().decide(message, timeout, context)

    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = [
            pool.submit(
                service(db_engine, ConcurrentProvider()).submit,
                conversation,
                str(uuid4()),
                "SP-1001",
            )
            for conversation in conversations
        ]
        assert all(task.result(timeout=15).status == "completed" for task in tasks)


def test_retries_are_bounded_across_restarts_and_new_messages_do_not_overtake(db_engine):
    conversation, request_id = new_conversation(db_engine), str(uuid4())
    provider = Mock()
    provider.decide.side_effect = ProviderUnavailable()
    assert (
        service(db_engine, provider).submit(conversation, request_id, "SP-1001").status
        == "retryable"
    )
    with pytest.raises(HTTPException):
        service(db_engine).submit(conversation, str(uuid4()), "SP-1001")
    assert (
        service(db_engine, provider).submit(conversation, request_id, "SP-1001").status
        == "retryable"
    )
    final = service(db_engine, provider).submit(conversation, request_id, "SP-1001")
    assert final.status == "failed" and final.response.order is None
    assert final.response.handoff.status == "queued"
    assert final.response.handoff.reason == "dependency_failure"
    assert service(db_engine, provider).submit(conversation, request_id, "SP-1001") == final
    assert provider.decide.call_count == 3
    detail = conversation_detail(db_engine, principal(db_engine), conversation)
    assert len(detail.messages) == 2
    assert service(db_engine).submit(conversation, str(uuid4()), "SP-1001").status == "completed"


def test_checkpoint_tables_exist_and_history_is_bounded(db_engine):
    conversation = new_conversation(db_engine)
    service(db_engine, conversation_max_turns=1).submit(conversation, str(uuid4()), "SP-1001")
    with pytest.raises(HTTPException) as full:
        service(db_engine, conversation_max_turns=1).submit(conversation, str(uuid4()), "SP-1001")
    assert full.value.status_code == 409
    with checkpoint_connection(db_engine) as conn:
        assert conn.execute("SELECT count(*) AS n FROM checkpoints").fetchone()["n"] > 0


def test_lost_lock_connection_cannot_publish_and_new_worker_recovers(db_engine):
    import psycopg

    conversation = new_conversation(db_engine)
    request_id = str(uuid4())
    turn = service(db_engine)
    original = turn.finalize

    def lose_connection(*args, **kwargs):
        turn.connection.close()
        return original(*args, **kwargs)

    with patch.object(turn, "finalize", side_effect=lose_connection), pytest.raises(psycopg.Error):
        turn.submit(conversation, request_id, "SP-1001")
    detail = conversation_detail(db_engine, principal(db_engine), conversation)
    assert len(detail.messages) == 1
    assert detail.runs[0].response is None
    recovered = service(db_engine).submit(conversation, request_id, "SP-1001")
    assert recovered.status == "completed"
    assert len(conversation_detail(db_engine, principal(db_engine), conversation).messages) == 2
