from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
import pytest
from app.auth import Principal
from app.handoffs import claim, reply, request_handoff, resolve, support_detail
from app.models import Conversation, Handoff, Message, ToolExecution, User
from app.provider import (
    Decision,
    DemoProvider,
    InvalidDecision,
    ProviderUnavailable,
    validate_decision,
)
from app.schemas import RequestHandoff, SupportReply
from conftest import create_chat, sign_in
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from test_conversations import new_conversation, principal, service

pytestmark = pytest.mark.integration


def staff(engine):
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email == "support@example.test"))
        return Principal(user.id, user.email, user.name, user.role, "test-csrf")


@pytest.fixture
def second_staff(db_engine):
    with Session(db_engine) as db, db.begin():
        original = db.get(User, staff(db_engine).id)
        user = User(
            email=f"{uuid4()}@example.test",
            name="Second support",
            role="support",
            password_hash=original.password_hash,
        )
        db.add(user)
        db.flush()
        actor = Principal(user.id, user.email, user.name, user.role, "test-csrf")
    yield actor
    with Session(db_engine) as db, db.begin():
        db.execute(delete(Conversation))
        db.execute(delete(User).where(User.id == actor.id))


def handoff(engine, conversation):
    return request_handoff(
        engine,
        principal(engine),
        conversation,
        RequestHandoff(reason="customer_request", summary="Please help with a damaged item."),
    )


def test_handoff_api_authorization_idempotency_and_support_login(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    path = f"/api/conversations/{conversation}/handoff"
    body = {"reason": "customer_request", "summary": "Please help me with my order."}
    assert client.post(path, json=body).status_code == 403
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued"
    assert client.post(path, headers=headers, json=body).json() == first.json()
    assert client.get("/api/support/handoffs").status_code == 403
    bob = sign_in(client, "bob@example.test")
    assert client.post(path, headers=bob, json=body).status_code == 404
    support = sign_in(client, "support@example.test")
    assert client.get("/api/auth/me").json()["role"] == "support"
    assert client.get(f"/api/conversations/{conversation}").status_code == 403
    assert client.get("/api/orders/SP-1001").status_code == 403
    queue = client.get("/api/support/handoffs").json()
    assert [row["id"] for row in queue] == [first.json()["id"]]
    assert client.post(f"/api/support/handoffs/{first.json()['id']}/claim").status_code == 403
    assert client.post("/api/auth/logout", headers=support).status_code == 204
    assert client.get("/api/support/handoffs").status_code == 401
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 1


def test_model_handoff_commits_once_and_recovers_after_tool_commit(db_engine):
    conversation = new_conversation(db_engine)
    request_id = str(uuid4())
    turn = service(db_engine)
    original = turn.request_handoff

    class Crash(BaseException):
        pass

    def crash_after_commit(arguments):
        original(arguments)
        raise Crash()

    with (
        patch.object(turn, "request_handoff", side_effect=crash_after_commit),
        pytest.raises(Crash),
    ):
        turn.submit(conversation, request_id, "I need a human agent")
    resumed = service(db_engine).submit(conversation, request_id, "I need a human agent")
    assert resumed.status == "completed" and resumed.response.outcome == "handoff"
    assert resumed.response.handoff.status == "queued"
    assert service(db_engine).submit(conversation, request_id, "I need a human agent") == resumed
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 1
        assert db.scalar(select(func.count()).select_from(ToolExecution)) == 1
        assert db.scalar(select(func.count()).select_from(Message)) == 2


def test_paused_turns_skip_provider_and_resolved_requires_new_conversation(db_engine):
    conversation = new_conversation(db_engine)
    saved = handoff(db_engine, conversation)
    provider = Mock()
    queued = service(db_engine, provider).submit(conversation, str(uuid4()), "SP-1001")
    assert queued.response.outcome == "handoff" and queued.response.order is None
    claim(db_engine, staff(db_engine), saved.id)
    assigned = service(db_engine, provider).submit(
        conversation, str(uuid4()), "What is the return policy?"
    )
    assert assigned.response.handoff.status == "assigned" and assigned.response.citations == []
    provider.decide.assert_not_called()
    resolve(db_engine, staff(db_engine), saved.id)
    with pytest.raises(HTTPException) as error:
        service(db_engine, provider).submit(conversation, str(uuid4()), "SP-1001")
    assert error.value.status_code == 409
    assert handoff(db_engine, conversation).status == "resolved"
    fresh = service(db_engine).submit(new_conversation(db_engine), str(uuid4()), "SP-1001")
    assert fresh.response.outcome == "verified"


def test_competing_claims_have_one_owner(db_engine, second_staff):
    saved = handoff(db_engine, new_conversation(db_engine))
    barrier = Barrier(2)
    actors = [staff(db_engine), second_staff]

    def attempt(actor):
        barrier.wait(timeout=5)
        try:
            return claim(db_engine, actor, saved.id)
        except HTTPException as error:
            assert error.status_code == 409
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, actors))
    assert sum(result is not None for result in results) == 1
    winner = actors[next(i for i, result in enumerate(results) if result)]
    assert claim(db_engine, winner, saved.id).assigned_to == winner.id


def test_human_reply_ownership_replay_sequence_and_resolution(db_engine, second_staff):
    conversation = new_conversation(db_engine)
    saved = handoff(db_engine, conversation)
    actor = staff(db_engine)
    body = SupportReply(request_id=uuid4(), message="I can help with the damaged item.")
    with pytest.raises(HTTPException):
        reply(db_engine, actor, saved.id, body)
    claim(db_engine, actor, saved.id)
    with pytest.raises(HTTPException):
        reply(db_engine, second_staff, saved.id, body)
    first = reply(db_engine, actor, saved.id, body)
    assert first.role == "support" and first.author_name == "Support" and first.run_id is None
    assert reply(db_engine, actor, saved.id, body) == first
    with pytest.raises(HTTPException) as error:
        reply(db_engine, actor, saved.id, body.model_copy(update={"message": "Changed content"}))
    assert error.value.status_code == 409
    service(db_engine).submit(conversation, str(uuid4()), "Thank you")
    with Session(db_engine) as db:
        messages = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sequence)
            )
        )
        assert [row.sequence for row in messages] == [1, 2, 3]
    with pytest.raises(HTTPException):
        resolve(db_engine, second_staff, saved.id)
    resolved = resolve(db_engine, actor, saved.id)
    assert resolve(db_engine, actor, saved.id) == resolved
    assert reply(db_engine, actor, saved.id, body) == first
    with pytest.raises(HTTPException):
        reply(db_engine, actor, saved.id, SupportReply(request_id=uuid4(), message="Too late"))


@pytest.mark.parametrize("provider_fails", [False, True])
def test_handoff_and_human_reply_during_model_call_fence_late_answer(db_engine, provider_fails):
    conversation = new_conversation(db_engine)
    started, release = Event(), Event()

    class SlowProvider(DemoProvider):
        def decide(self, message, timeout, context=None):
            started.set()
            assert release.wait(10)
            if provider_fails:
                raise ProviderUnavailable()
            return super().decide(message, timeout, context)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(
            service(db_engine, SlowProvider()).submit, conversation, str(uuid4()), "SP-1001"
        )
        try:
            assert started.wait(5)
            saved = handoff(db_engine, conversation)
            actor = staff(db_engine)
            claim(db_engine, actor, saved.id)
            reply(
                db_engine,
                actor,
                saved.id,
                SupportReply(request_id=uuid4(), message="I am checking this for you."),
            )
            resolve(db_engine, actor, saved.id)
        finally:
            release.set()
        result = task.result(timeout=10)
    assert result.response.outcome == "handoff"
    assert result.response.handoff.status == "resolved"
    assert result.response.order is None and result.response.citations == []
    with Session(db_engine) as db:
        rows = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sequence)
            )
        )
        assert [row.sequence for row in rows] == [1, 2, 3]
        assert "shipped" not in rows[1].content
        assert rows[2].role == "support"


def test_completed_checkpoint_cannot_publish_after_handoff(db_engine):
    conversation = new_conversation(db_engine)
    request_id = str(uuid4())
    turn = service(db_engine)
    with (
        patch.object(turn, "finalize", side_effect=RuntimeError("crash")),
        pytest.raises(RuntimeError),
    ):
        turn.submit(conversation, request_id, "SP-1001")
    handoff(db_engine, conversation)
    recovered = service(db_engine).submit(conversation, request_id, "SP-1001")
    assert recovered.response.outcome == "handoff" and recovered.response.order is None


def test_failed_queue_transaction_never_reports_transfer(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    with patch("app.handoffs.handoff_view", side_effect=psycopg.OperationalError("outage")):
        response = client.post(
            f"/api/conversations/{conversation}/handoff",
            headers=headers,
            json={"reason": "customer_request", "summary": "Help please"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service temporarily unavailable"}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 0


def test_support_context_is_scoped_and_customer_cannot_forge_author(client, db_engine):
    headers = sign_in(client)
    conversation = create_chat(client, headers)
    service(db_engine).submit(conversation, str(uuid4()), "SP-1001")
    saved = handoff(db_engine, conversation)
    detail = support_detail(db_engine, staff(db_engine), saved.id)
    assert [row["reference"] for row in detail["orders"]] == ["SP-1001"]
    assert detail["attempted_actions"][0]["tool"] == "lookup_order"
    path = f"/api/support/handoffs/{saved.id}"
    assert client.get(path).status_code == 403
    assert (
        client.post(
            path + "/replies",
            headers=headers,
            json={"request_id": str(uuid4()), "message": "Forged support"},
        ).status_code
        == 403
    )
    support = sign_in(client, "support@example.test")
    assert client.post(path + "/claim", headers=support).status_code == 200
    assert (
        client.post(
            path + "/replies",
            headers=support,
            json={"request_id": str(uuid4()), "message": "Forged support", "author_id": "other"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/replies",
            headers=support,
            json={"request_id": str(uuid4()), "message": "Here is a human response."},
        ).status_code
        == 200
    )
    sign_in(client)
    history = client.get(f"/api/conversations/{conversation}").json()
    assert history["handoff"]["status"] == "assigned"
    assert history["messages"][-1]["role"] == "support"
    assert history["messages"][-1]["author_name"] == "Support"


def test_handoff_tool_rejects_model_identity():
    with pytest.raises(InvalidDecision):
        validate_decision(
            Decision(
                action="request_handoff",
                arguments={
                    "reason": "customer_request",
                    "summary": "Help",
                    "customer_id": "someone-else",
                },
            )
        )


def test_human_replies_do_not_consume_customer_turn_budget(db_engine):
    conversation = new_conversation(db_engine)
    saved = handoff(db_engine, conversation)
    actor = staff(db_engine)
    claim(db_engine, actor, saved.id)
    for index in range(3):
        reply(
            db_engine,
            actor,
            saved.id,
            SupportReply(request_id=uuid4(), message=f"Human reply {index}"),
        )
    for index in range(2):
        result = service(db_engine, conversation_max_turns=2).submit(
            conversation, str(uuid4()), f"Customer reply {index}"
        )
        assert result.status == "completed"
    with pytest.raises(HTTPException) as error:
        service(db_engine, conversation_max_turns=2).submit(
            conversation, str(uuid4()), "Over budget"
        )
    assert error.value.status_code == 409
