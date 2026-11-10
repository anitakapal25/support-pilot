from datetime import timedelta
from uuid import uuid4

import pytest
from app.config import Settings
from app.main import create_app
from app.maintenance import run_maintenance
from app.models import Conversation, Handoff, OperationalCounter, Order, User, utcnow
from app.provider import DemoProvider
from app.seed import DEMO_PASSWORD
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

ORIGIN = "https://testserver"
TOKEN = "t" * 40
HEADERS = {"X-Requested-With": "support-pilot", "Origin": ORIGIN}


def demo_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "demo",
        "cookie_secure": True,
        "allowed_origins": [ORIGIN],
        "openai_api_key": "sk-test-not-used",
        "openai_model": "test-model",
        # Pinned explicitly: CI exports AGENT_PROVIDER=demo, which would otherwise win.
        "agent_provider": "openai",
        "embedding_provider": "openai",
        "request_limits_enabled": True,
        "spend_limits_enabled": True,
        "model_input_usd_per_million": 1,
        "model_output_usd_per_million": 1,
        "embedding_usd_per_million": 1,
        "maintenance_token": TOKEN,
    }
    return Settings(**{**values, **overrides})


def clear_guests(engine):
    with Session(engine) as db, db.begin():
        guests = select(User.id).where(User.demo_group.is_not(None))
        db.execute(delete(Conversation).where(Conversation.customer_id.in_(guests)))
        db.execute(delete(Order).where(Order.customer_id.in_(guests)))
        db.execute(delete(User).where(User.demo_group.is_not(None)))
        db.execute(delete(OperationalCounter))


@pytest.fixture
def demo_engine(db_engine):
    clear_guests(db_engine)
    yield db_engine
    clear_guests(db_engine)


def demo_client(engine, **overrides):
    # Explicit offline provider: no network calls even though the profile names OpenAI.
    app = create_app(demo_settings(**overrides), engine, DemoProvider())
    return TestClient(app, base_url=ORIGIN)


def start_guest(client):
    response = client.post("/api/demo/session", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["guest"] is True and body["role"] == "customer"
    return {"X-CSRF-Token": body["csrf_token"], "Origin": ORIGIN}


def queue_handoff(client, headers):
    conversation = client.post(
        "/api/conversations", headers=headers, json={"request_id": str(uuid4())}
    ).json()["id"]
    response = client.post(
        f"/api/conversations/{conversation}/handoff",
        headers=headers,
        json={"reason": "customer_request", "summary": "Please help"},
    )
    assert response.status_code == 200, response.text
    return conversation, response.json()["id"]


def switch(client, headers, role):
    response = client.post("/api/demo/switch", headers=headers, json={"role": role})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": ORIGIN}


def test_demo_profile_requires_internet_facing_guards():
    with pytest.raises(ValidationError):
        demo_settings(cookie_secure=False)
    with pytest.raises(ValidationError):
        demo_settings(allowed_origins=["http://example.test"])
    with pytest.raises(ValidationError):
        demo_settings(agent_provider="demo")
    with pytest.raises(ValidationError):
        demo_settings(maintenance_token="short")


def test_demo_routes_hidden_outside_hosted_demo(client):
    assert client.post("/api/demo/session", headers=HEADERS).status_code == 404
    assert client.post("/api/internal/maintenance").status_code == 404
    assert client.get("/api/config").json()["hosted_demo"] is False


def test_guest_sees_own_order_but_not_neighbours(demo_engine):
    with demo_client(demo_engine) as client:
        headers = start_guest(client)
        assert client.get("/api/auth/me").json()["guest"] is True
        orders = client.get("/api/demo/orders").json()
        assert orders["own"] != orders["other"]
        assert client.get(f"/api/orders/{orders['own']}").status_code == 200
        assert client.get(f"/api/orders/{orders['other']}").status_code == 404
        chat = client.post(
            "/api/conversations", headers=headers, json={"request_id": str(uuid4())}
        ).json()["id"]
        reply = client.post(
            f"/api/conversations/{chat}/messages",
            headers=headers,
            json={"request_id": str(uuid4()), "message": f"Where is {orders['other']}?"},
        ).json()["response"]
        assert reply["outcome"] == "unavailable" and reply.get("order") is None


def test_guest_accounts_cannot_use_password_login(demo_engine):
    with demo_client(demo_engine) as client:
        start_guest(client)
        email = client.get("/api/auth/me").json()["email"]
        response = client.post(
            "/api/auth/login", headers=HEADERS, json={"email": email, "password": DEMO_PASSWORD}
        )
        assert response.status_code in (401, 422)


def test_guest_support_view_is_scoped_to_own_group(demo_engine):
    with demo_client(demo_engine) as a, demo_client(demo_engine) as b:
        a_headers, b_headers = start_guest(a), start_guest(b)
        _, a_handoff = queue_handoff(a, a_headers)
        _, b_handoff = queue_handoff(b, b_headers)
        support = switch(a, a_headers, "support")
        assert a.get("/api/auth/me").json()["role"] == "support"
        queued = [item["id"] for item in a.get("/api/support/handoffs?status=queued").json()]
        assert queued == [a_handoff]
        assert a.get(f"/api/support/handoffs/{b_handoff}").status_code == 404
        assert (
            a.post(f"/api/support/handoffs/{b_handoff}/claim", headers=support).status_code == 404
        )
        assert (
            a.post(f"/api/support/handoffs/{a_handoff}/claim", headers=support).status_code == 200
        )
        # Switching back returns to the same guest customer.
        switch(a, support, "customer")
        assert a.get("/api/auth/me").json()["role"] == "customer"


def test_regular_support_never_sees_guest_handoffs(demo_engine, client):
    with demo_client(demo_engine) as guest:
        queue_handoff(guest, start_guest(guest))
    from conftest import sign_in

    sign_in(client, "support@example.test")
    assert client.get("/api/support/handoffs?status=queued").json() == []


def test_guest_creation_is_rate_limited_per_ip(demo_engine):
    with demo_client(demo_engine, guest_sessions_per_ip_hour=2) as client:
        start_guest(client)
        start_guest(client)
        assert client.post("/api/demo/session", headers=HEADERS).status_code == 429


def test_maintenance_requires_token_and_purges_expired_groups(demo_engine):
    with demo_client(demo_engine) as expired, demo_client(demo_engine) as fresh:
        queue_handoff(expired, start_guest(expired))
        queue_handoff(fresh, start_guest(fresh))
        with Session(demo_engine) as db, db.begin():
            group = db.scalar(
                select(User.demo_group)
                .join(Conversation, Conversation.customer_id == User.id)
                .limit(1)
            )
            db.execute(
                update(User)
                .where(User.demo_group == group)
                .values(demo_expires_at=utcnow() - timedelta(hours=1))
            )
        assert group is not None
        assert expired.post("/api/internal/maintenance").status_code == 401
        wrong = {"Authorization": "Bearer " + "x" * 40}
        assert expired.post("/api/internal/maintenance", headers=wrong).status_code == 401
        response = expired.post(
            "/api/internal/maintenance", headers={"Authorization": "Bearer " + TOKEN}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deleted_guest_groups"] == 1
        assert 0 <= body["spend"]["fraction"] <= 1
    with Session(demo_engine) as db:
        assert not db.scalar(select(func.count(User.id)).where(User.demo_group == group))
        remaining = db.scalar(select(func.count(func.distinct(User.demo_group))))
        assert remaining == 1
        assert db.scalar(select(func.count(Handoff.id))) == 1


def test_run_maintenance_keeps_unexpired_guests(demo_engine):
    with demo_client(demo_engine) as client:
        start_guest(client)
    result = run_maintenance(demo_engine, demo_settings())
    assert result["deleted_guest_groups"] == 0
    with Session(demo_engine) as db:
        assert db.scalar(select(func.count(User.id)).where(User.demo_group.is_not(None))) == 3
