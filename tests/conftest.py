import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.checkpoints import setup_checkpoints
from app.config import Settings
from app.main import create_app
from app.models import Conversation, LoginRateLimit, LoginSession
from app.provider import DemoProvider
from app.seed import DEMO_PASSWORD, seed_demo
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


@pytest.fixture(scope="session")
def migrated_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL auth/order tests")
    # Every test run owns an isolated schema; never truncate application tables.
    schema = "test_m5_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        make_url(url).update_query_dict({"options": f"-c search_path={schema},public"})
    )
    try:
        config = Config("alembic.ini")
        config.attributes["version_table_schema"] = schema
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        seed_demo(engine)
        setup_checkpoints(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def db_engine(migrated_engine):
    with Session(migrated_engine) as db, db.begin():
        db.execute(delete(Conversation))
        db.execute(delete(LoginSession))
        db.execute(delete(LoginRateLimit))
    return migrated_engine


@pytest.fixture
def client(db_engine):
    settings = Settings(
        _env_file=None, agent_provider="demo", allowed_origins=["http://testserver"]
    )
    with TestClient(create_app(settings, db_engine, DemoProvider())) as test_client:
        yield test_client


def sign_in(client, email="alice@example.test"):
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": DEMO_PASSWORD},
        headers={"X-Requested-With": "support-pilot"},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_chat(client, headers):
    response = client.post("/api/conversations", headers=headers, json={"request_id": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def send_chat(client, headers, body):
    conversation_id = create_chat(client, headers)
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"request_id": str(uuid4()), **body},
    )
