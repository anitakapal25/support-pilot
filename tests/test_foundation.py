from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from app.main import create_app
from app.schemas import OrderStatus
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError


def test_health_and_openapi():
    # SQLite checks routing; the marked integration test checks real PostgreSQL.
    engine = create_engine("sqlite://")
    with TestClient(create_app(engine=engine)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert "/health/ready" in client.get("/openapi.json").json()["paths"]
    engine.dispose()


def test_database_outage_does_not_kill_liveness_or_leak_details():
    engine = Mock()
    engine.connect.side_effect = OperationalError("secret-password", {}, Exception())
    with TestClient(create_app(engine=engine)) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}


def test_order_contract_rejects_unknown_status_and_identity():
    fields = {"order_reference": "DEMO-1001", "checked_at": datetime.now(UTC)}
    with pytest.raises(ValidationError):
        OrderStatus(**fields, status="refunded")
    with pytest.raises(ValidationError):
        OrderStatus(**fields, status="shipped", customer_id="someone-else")


@pytest.mark.integration
def test_real_postgres_readiness():
    import os

    from app.config import Settings

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL integration test")
    with TestClient(create_app(Settings(database_url=url))) as client:
        assert client.get("/health/ready").status_code == 200
