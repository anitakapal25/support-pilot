import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from app.agent import run_agent
from app.auth import Principal
from app.config import Settings
from app.provider import (
    Decision,
    DemoProvider,
    InvalidDecision,
    OpenAIProvider,
    ProviderUnavailable,
    TransientProviderError,
)
from app.schemas import OrderStatus
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


CUSTOMER = Principal("alice", "alice@example.test", "Alice", "customer", "csrf")


def test_agent_only_reports_verified_tool_result():
    commerce = Mock()
    commerce.lookup_order.return_value = OrderStatus(
        order_reference="SP-1001", status="delivered", checked_at=datetime.now(UTC)
    )
    result = run_agent("SP-1001", CUSTOMER, commerce, DemoProvider(), settings())
    assert result.outcome == "verified"
    assert result.message == "Order SP-1001 is delivered."
    commerce.lookup_order.assert_called_once_with(CUSTOMER, "SP-1001")


def test_model_supplied_identity_is_rejected_before_tool_execution():
    provider, commerce = Mock(), Mock()
    provider.decide.return_value = Decision(
        action="lookup_order", arguments={"order_reference": "SP-2001", "customer_id": "bob"}
    )
    result = run_agent("message", CUSTOMER, commerce, provider, settings())
    assert result.outcome == "error" and result.order is None
    commerce.lookup_order.assert_not_called()


def test_transient_retry_budget_and_permanent_failure():
    provider, commerce = Mock(), Mock()
    provider.decide.side_effect = TransientProviderError()
    assert run_agent("message", CUSTOMER, commerce, provider, settings()).outcome == "error"
    assert provider.decide.call_count == 3
    commerce.lookup_order.assert_not_called()
    provider.reset_mock()
    provider.decide.side_effect = ProviderUnavailable()
    assert run_agent("message", CUSTOMER, commerce, provider, settings()).outcome == "error"
    assert provider.decide.call_count == 1


def test_tool_failure_never_claims_success():
    commerce = Mock()
    commerce.lookup_order.side_effect = OperationalError("sensitive", {}, Exception())
    result = run_agent("SP-1001", CUSTOMER, commerce, DemoProvider(), settings())
    assert result.outcome == "error" and result.order is None
    assert "sensitive" not in result.message


def test_deadline_prevents_late_result():
    provider, commerce = Mock(), Mock()
    provider.decide.return_value = Decision(
        action="lookup_order", arguments={"order_reference": "SP-1001"}
    )
    # Deadline is 30; it expires while the provider is running.
    with patch("app.agent.time.monotonic", side_effect=[0, 1, 31]):
        result = run_agent("SP-1001", CUSTOMER, commerce, provider, settings())
    assert result.outcome == "error"
    commerce.lookup_order.assert_not_called()


def test_production_disallows_demo_and_insecure_cookies():
    with pytest.raises(ValidationError):
        settings(app_env="production", agent_provider="demo", cookie_secure=True)
    with pytest.raises(ValidationError):
        settings(app_env="production", cookie_secure=False)


def test_openai_adapter_validates_output_and_disables_storage_and_sdk_retries():
    with patch("app.provider.AsyncOpenAI") as sdk:
        client = MagicMock()
        client.responses.create = AsyncMock()
        sdk.return_value.__aenter__.return_value = client
        client.responses.create.return_value = SimpleNamespace(
            status="completed",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="lookup_order",
                    arguments='{"order_reference":"SP-1001"}',
                )
            ],
        )
        provider = OpenAIProvider(
            settings(openai_api_key="test-not-real", openai_model="configured-model")
        )
        assert provider.decide("SP-1001", 2).arguments == {"order_reference": "SP-1001"}
        assert sdk.call_args.kwargs["max_retries"] == 0
        call = client.responses.create.call_args.kwargs
        assert call["store"] is False and call["parallel_tool_calls"] is False
        assert call["max_output_tokens"] == 256
        client.responses.create.return_value.output.append(
            client.responses.create.return_value.output[0]
        )
        with pytest.raises(InvalidDecision):
            provider.decide("SP-1001", 2)


def test_missing_openai_configuration_fails_closed():
    with pytest.raises(ProviderUnavailable):
        OpenAIProvider(settings(openai_api_key=None, openai_model="")).decide("SP-1001", 2)


def test_provider_deadline_cancels_pending_http_work():
    provider = OpenAIProvider(settings())
    cancelled = []

    async def pending(message, timeout):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    with patch.object(provider, "_decide", pending), pytest.raises(TransientProviderError):
        provider.decide("SP-1001", 0.01)
    assert cancelled == [True]
