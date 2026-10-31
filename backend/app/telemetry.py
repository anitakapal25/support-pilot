"""Allowlisted telemetry: no prompts, response text, order refs, headers or exception text."""

import json
import logging
import math
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AuditEvent, RequestTrace, utcnow

logger = logging.getLogger("support_pilot.telemetry")
ACTIONS = {
    "lookup_order",
    "lookup_order_and_policy",
    "search_policy",
    "ask_order_reference",
    "unsupported_request",
    "prepare_ticket",
    "request_handoff",
}
OUTCOMES = {
    "verified",
    "clarification",
    "unavailable",
    "unsupported",
    "error",
    "policy",
    "handoff",
    "queued",
    "assigned",
    "resolved",
    "open",
    "pending",
    "confirmed",
    "replayed",
    "completed",
    "failed",
    "retryable",
    "allowed",
    "denied",
    "invalid_citations",
    "input_limit",
    "missing_usage",
    "ok",
    "unknown",
}
EVENTS = {
    "agent.route",
    "agent.accepted",
    "agent.completed",
    "agent.failure",
    "tool.result",
    "retrieval.result",
    "provider.usage",
    "provider.attempt",
    "provider.failure",
    "citation.validation",
    "audit",
    "auth.authorized",
}
AUDIT_ACTIONS = {
    "ticket.draft_saved",
    "ticket.created",
    "handoff.queued",
    "handoff.claimed",
    "handoff.resolved",
    "support.replied",
    "auth.login",
    "auth.logout",
}
CURRENT: ContextVar = ContextVar("support_pilot_trace", default=None)


def identifier(value):
    try:
        return str(UUID(str(value)))
    except ValueError, TypeError, AttributeError:
        return None


@dataclass
class TraceBuffer:
    id: str = field(default_factory=lambda: str(uuid4()))
    started_at: object = field(default_factory=utcnow)
    clock: float = field(default_factory=time.perf_counter)
    actor_id: str | None = None
    conversation_id: str | None = None
    events: list = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)
    dropped_events: int = 0


def bind(*, actor_id=None, conversation_id=None):
    trace = CURRENT.get()
    if trace:
        if identifier(actor_id):
            trace.actor_id = identifier(actor_id)
        if identifier(conversation_id):
            trace.conversation_id = identifier(conversation_id)


def emit(event, **attributes):
    trace = CURRENT.get()
    if not trace or event not in EVENTS:
        return
    safe = {}
    for key, value in attributes.items():
        if key in {"run_id", "resource_id"} and identifier(value):
            safe[key] = identifier(value)
        elif key in {"source_ids"} and isinstance(value, list):
            safe[key] = [identifier(item) for item in value[:20] if identifier(item)]
        elif (
            key == "action"
            and isinstance(value, str)
            and value in ACTIONS | AUDIT_ACTIONS
            or key == "outcome"
            and isinstance(value, str)
            and value in OUTCOMES
            or key == "provider"
            and isinstance(value, str)
            and value in {"demo", "openai", "embedding_demo", "embedding_openai"}
        ):
            safe[key] = value
        elif key in {"input_tokens", "output_tokens", "cached_tokens", "attempt", "count"}:
            if type(value) is int and 0 <= value <= 10**9:
                safe[key] = value
        elif key in {"duration_ms", "cost_usd"}:
            if value is None:
                safe[key] = None
            elif type(value) in {int, float} and math.isfinite(value) and value >= 0:
                safe[key] = round(value, 8)
        elif key == "usage_known" and type(value) is bool:
            safe[key] = value
    with trace.lock:
        if len(trace.events) < 96:
            trace.events.append(
                {
                    "event": event,
                    "elapsed_ms": round((time.perf_counter() - trace.clock) * 1000, 3),
                    **safe,
                }
            )
        else:
            trace.dropped_events += 1


def record_usage(provider, usage, settings):
    """Unknown usage/pricing stays null; configured rates are estimates, never a bill."""
    if provider in {"demo", "embedding_demo"}:
        emit(
            "provider.usage",
            provider=provider,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=0,
            usage_known=True,
        )
        return
    input_tokens = getattr(
        usage, "prompt_tokens" if provider == "embedding_openai" else "input_tokens", None
    )
    output_tokens = 0 if provider == "embedding_openai" else getattr(usage, "output_tokens", None)
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0)
    known = all(
        type(value) is int and value >= 0 for value in (input_tokens, output_tokens, cached)
    )
    known = known and cached <= input_tokens
    cost = None
    if known:
        if provider == "embedding_openai" and settings.embedding_usd_per_million is not None:
            cost = input_tokens * settings.embedding_usd_per_million / 1_000_000
        elif (
            provider == "openai"
            and all(
                value is not None
                for value in (
                    settings.model_input_usd_per_million,
                    settings.model_output_usd_per_million,
                )
            )
            and (not cached or settings.model_cached_input_usd_per_million is not None)
        ):
            cost = (
                (input_tokens - cached) * settings.model_input_usd_per_million
                + cached * (settings.model_cached_input_usd_per_million or 0)
                + output_tokens * settings.model_output_usd_per_million
            ) / 1_000_000
    emit(
        "provider.usage",
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        cost_usd=cost,
        usage_known=known,
    )


def persist_trace(engine, trace, method, route, status_code):
    # Route is the framework's static template, never the raw URL or query string.
    payload = {
        "id": trace.id,
        "started_at": trace.started_at,
        "duration_ms": round((time.perf_counter() - trace.clock) * 1000, 3),
        "method": method
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        else "OTHER",
        "route": route,
        "status_code": status_code,
        "actor_id": trace.actor_id,
        "conversation_id": trace.conversation_id,
        "events": trace.events,
        "dropped_events": trace.dropped_events,
    }
    try:
        with Session(engine) as db, db.begin():
            db.add(RequestTrace(**payload))
    except SQLAlchemyError:
        # Do not turn an already-committed ticket into a failure because telemetry is down.
        # Durable action audits are independently part of each business transaction.
        logger.warning(json.dumps({"event": "trace_persistence_failed", **payload}, default=str))
    else:
        logger.info(json.dumps({"event": "request_completed", **payload}, default=str))


def audit_session(db, actor_id, action, resource_id, conversation_id=None):
    if action not in AUDIT_ACTIONS:
        raise ValueError("Unknown audit action")
    trace = CURRENT.get()
    db.add(
        AuditEvent(
            id=str(uuid4()),
            actor_id=str(actor_id),
            action=action,
            resource_id=str(resource_id),
            conversation_id=conversation_id,
            trace_id=trace.id if trace else None,
        )
    )
    emit("audit", action=action, resource_id=resource_id, outcome="ok")


def audit_connection(conn, actor_id, action, resource_id, conversation_id=None):
    if action not in AUDIT_ACTIONS:
        raise ValueError("Unknown audit action")
    trace = CURRENT.get()
    conn.execute(
        "INSERT INTO audit_events(id,actor_id,action,resource_id,conversation_id,trace_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,now())",
        (
            str(uuid4()),
            str(actor_id),
            action,
            str(resource_id),
            conversation_id,
            trace.id if trace else None,
        ),
    )
    emit("audit", action=action, resource_id=resource_id, outcome="ok")
