"""Bounded graph. Persistence and request recovery are coordinated by conversations.py."""

import logging
import time
from collections.abc import Callable
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.exc import SQLAlchemyError

from app.auth import Principal
from app.config import Settings
from app.orders import CommerceAdapter, OrderUnavailable
from app.provider import (
    DecisionProvider,
    ProviderUnavailable,
    TransientProviderError,
    validate_decision,
)
from app.schemas import ChatResponse, OrderStatus
from app.telemetry import emit

logger = logging.getLogger(__name__)


class State(TypedDict, total=False):
    message: str
    action: str
    arguments: dict
    order: dict
    outcome: str
    answer: str
    context: dict
    citations: list
    policy_answer: str
    handoff: dict


def build_agent(
    principal: Principal,
    commerce: CommerceAdapter,
    provider: DecisionProvider,
    settings: Settings,
    *,
    checkpointer=None,
    retrieve: Callable | None = None,
    handoff: Callable | None = None,
    before_model: Callable[[], None] = lambda: None,
    guard: Callable[[], None] = lambda: None,
):
    deadline = time.monotonic() + settings.agent_timeout_seconds

    def route(state: State) -> dict:
        for attempt in range(settings.provider_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderUnavailable()
            try:
                before_model()
                decision = validate_decision(
                    provider.decide(
                        state["message"],
                        min(settings.provider_timeout_seconds, remaining),
                        **({"context": state["context"]} if state.get("context") else {}),
                    )
                )
                guard()
                if time.monotonic() >= deadline:
                    raise ProviderUnavailable()
                emit("agent.route", action=decision.action)
                return {"action": decision.action, "arguments": decision.arguments}
            except TransientProviderError:
                if attempt == settings.provider_retries:
                    raise
                time.sleep(min(0.1 * (2**attempt), max(0, deadline - time.monotonic())))
        raise ProviderUnavailable()

    def lookup(state: State) -> dict:
        if time.monotonic() >= deadline:
            raise ProviderUnavailable()
        try:
            order = commerce.lookup_order(principal, state["arguments"]["order_reference"])
        except OrderUnavailable:
            return {
                "outcome": "unavailable",
                "answer": "That order is not available for your account. Check the reference and try again.",
            }
        guard()
        if time.monotonic() >= deadline:
            raise ProviderUnavailable()
        return {"outcome": "verified", "order": order.model_dump(mode="json")}

    def policy(state: State) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or retrieve is None:
            raise ProviderUnavailable()
        result = retrieve(state["message"], min(settings.provider_timeout_seconds, remaining))
        guard()
        if time.monotonic() >= deadline:
            raise ProviderUnavailable()
        return result

    def transfer(state: State) -> dict:
        if handoff is None:
            raise ProviderUnavailable()
        return handoff(state["arguments"])

    def respond(state: State) -> dict:
        if state["action"] == "prepare_ticket":
            return {
                "outcome": "clarification",
                "answer": "Use the Support ticket form below to describe the issue. Review the saved details, then select Confirm and create ticket. No ticket is created before confirmation.",
            }
        if state["action"] == "ask_order_reference":
            return {
                "outcome": "clarification",
                "answer": "Please send one order reference, for example SP-1001, so I can check its status.",
            }
        if state["action"] == "unsupported_request":
            return {
                "outcome": "unsupported",
                "answer": "I can check order status and explain published policies. You can also create a support ticket using the form. You can request human support. Refunds and cancellations are not automated here.",
            }
        if state.get("policy_answer"):
            order_answer = ""
            if state.get("order"):
                order = OrderStatus.model_validate(state["order"])
                order_answer = f"Order {order.order_reference} is {order.status}.\n\n"
            elif state.get("outcome") == "unavailable":
                order_answer = state["answer"] + "\n\n"
            return {
                "outcome": "policy" if state.get("citations") else "clarification",
                "answer": order_answer + state["policy_answer"],
            }
        if state.get("outcome") == "verified":
            order = OrderStatus.model_validate(state["order"])
            return {"answer": f"Order {order.order_reference} is {order.status}."}
        return {}

    builder = StateGraph(State)
    builder.add_node("route", route)
    builder.add_node("lookup", lookup)
    builder.add_node("respond", respond)
    builder.add_node("policy", policy)
    builder.add_node("handoff", transfer)
    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        lambda state: (
            "lookup"
            if state["action"] in ("lookup_order", "lookup_order_and_policy")
            else "policy"
            if state["action"] == "search_policy"
            else "handoff"
            if state["action"] == "request_handoff"
            else "respond"
        ),
    )
    builder.add_conditional_edges(
        "lookup",
        lambda state: "policy" if state["action"] == "lookup_order_and_policy" else "respond",
    )
    builder.add_edge("policy", "respond")
    builder.add_edge("handoff", "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


def run_agent(
    message: str,
    principal: Principal,
    commerce: CommerceAdapter,
    provider: DecisionProvider,
    settings: Settings,
) -> ChatResponse:
    """Small nonpersistent runner for component tests; HTTP chat uses durable service."""
    request_id = str(uuid4())
    try:
        # Acyclic graph: one authorized read at most, three model attempts at most.
        state = build_agent(principal, commerce, provider, settings).invoke(
            {"message": message}, {"recursion_limit": 6}
        )
        result = ChatResponse(
            request_id=request_id,
            outcome=state["outcome"],
            message=state["answer"],
            order=state.get("order"),
        )
    except ProviderUnavailable, SQLAlchemyError:
        result = ChatResponse(
            request_id=request_id,
            outcome="error",
            message="Order lookup is temporarily unavailable. Please try again later.",
        )
    # Only structured outcomes, never customer text, model reasoning, cookies, or credentials.
    logger.info("agent_completed request_id=%s outcome=%s", request_id, result.outcome)
    return result
