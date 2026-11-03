"""Shared PostgreSQL admission controls and conservative provider spend reservations."""

import math
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import OperationalCounter, PilotControl, UsageReservation, utcnow


class SpendLimitExceeded(Exception):
    pass


def consume(db, key, amount, limit, expires_at):
    db.execute(
        insert(OperationalCounter)
        .values(key=key, used=0, expires_at=expires_at)
        .on_conflict_do_nothing()
    )
    counter = db.scalar(
        select(OperationalCounter).where(OperationalCounter.key == key).with_for_update()
    )
    if counter.used + amount > limit:
        return False
    counter.used += amount
    return True


def admission(engine, settings, actor, *, automation=False):
    now = utcnow()
    with Session(engine) as db, db.begin():
        control = db.get(PilotControl, "pilot")
        if automation and (not settings.pilot_enabled or control and not control.enabled):
            raise HTTPException(
                503, "Automated assistance is paused. You can still request human support."
            )
        percent = min(
            settings.pilot_admission_percent, control.admission_percent if control else 100
        )
        if automation and int(actor.id.replace("-", ""), 16) % 100 >= percent:
            raise HTTPException(
                503,
                "Automated assistance is not enabled for this account. Human support is available.",
            )
        if settings.request_limits_enabled and not automation:
            bucket = int(now.timestamp()) // 60
            if not consume(
                db,
                f"request:{actor.id}:{bucket}",
                1,
                settings.requests_per_minute,
                now + timedelta(minutes=2),
            ):
                raise HTTPException(
                    429, "Request limit reached; retry shortly.", headers={"Retry-After": "60"}
                )


def reserve_spend(engine, settings, provider, input_tokens, output_tokens=0):
    if not settings.spend_limits_enabled:
        return None
    if engine is None:
        raise SpendLimitExceeded("A database is required for spend admission")
    input_rate = (
        settings.embedding_usd_per_million
        if provider == "embedding_openai"
        else settings.model_input_usd_per_million
    )
    output_rate = 0 if provider == "embedding_openai" else settings.model_output_usd_per_million
    if input_rate is None or output_rate is None:
        raise SpendLimitExceeded("Provider rates must be configured")
    # Reserve full uncached input and maximum output. Ambiguous failures retain the reservation.
    amount = max(1, math.ceil(input_tokens * input_rate + output_tokens * output_rate))
    now = utcnow()
    month = now.strftime("%Y-%m")
    with Session(engine) as db, db.begin():
        if not consume(
            db,
            "spend:" + month,
            amount,
            math.floor(settings.monthly_model_budget_usd * 1_000_000),
            now + timedelta(days=62),
        ):
            raise SpendLimitExceeded("Monthly provider allowance exhausted")
        id = str(uuid4())
        db.add(
            UsageReservation(
                id=id, month=month, provider=provider, reserved_micro_usd=amount, created_at=now
            )
        )
        return id
