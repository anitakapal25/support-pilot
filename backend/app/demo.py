"""Hosted public demo: one private synthetic guest group per visitor.

A group holds a customer (the visitor), a neighbour customer whose order demonstrates
non-disclosure, and a support agent the visitor can switch to. Support queries are
scoped to the agent's group, so visitors never see each other's conversations.
Guests have no usable password and are deleted by maintenance after their TTL.
"""

import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import _DUMMY_HASH, COOKIE_NAME, Principal, check_origin, start_session
from app.models import AuditEvent, Order, OrderItem, RequestTrace, User, utcnow
from app.operations import consume
from app.telemetry import audit_session

# Seeded development orders use SP-1001/SP-2001; guests draw from a disjoint range.
REFERENCE_RANGE = (3000, 9000)


def require_hosted_demo(request: Request) -> None:
    if request.app.state.settings.app_env != "demo":
        raise HTTPException(404, "Not found")


def session_hours(settings) -> float:
    return min(settings.session_hours, settings.guest_ttl_hours)


def add_order(db, user, status, delivery_days):
    for _ in range(20):
        reference = (
            f"SP-{secrets.randbelow(REFERENCE_RANGE[1] - REFERENCE_RANGE[0]) + REFERENCE_RANGE[0]}"
        )
        try:
            with db.begin_nested():
                order = Order(
                    reference=reference,
                    customer_id=user.id,
                    status=status,
                    estimated_delivery=utcnow() + timedelta(days=delivery_days),
                )
                db.add(order)
                db.flush()
        except IntegrityError:
            continue
        db.add(OrderItem(order_id=order.id, name="Everyday canvas tote", quantity=1))
        return reference
    raise HTTPException(503, "Demo is busy; try again shortly")


def admit_guest(db, settings, ip):
    now = utcnow()
    hour = now.strftime("%Y-%m-%dT%H")
    allowed = consume(
        db,
        f"guest-ip:{ip}:{hour}",
        1,
        settings.guest_sessions_per_ip_hour,
        now + timedelta(hours=2),
    ) and consume(
        db,
        "guest-day:" + now.strftime("%Y-%m-%d"),
        1,
        settings.guest_sessions_per_day,
        now + timedelta(days=2),
    )
    if not allowed:
        raise HTTPException(
            429, "Demo sign-ups are busy; try again later", headers={"Retry-After": "3600"}
        )


def create_guest(request: Request) -> tuple[User, str, str, dict]:
    """Create a guest group and sign the visitor in as its customer."""
    require_hosted_demo(request)
    check_origin(request)
    if request.headers.get("x-requested-with") != "support-pilot":
        raise HTTPException(403, "Missing request header")
    settings = request.app.state.settings
    ip = request.client.host if request.client else "unknown"
    group = str(uuid4())
    expires = utcnow() + timedelta(hours=settings.guest_ttl_hours)
    with Session(request.app.state.database, expire_on_commit=False) as db, db.begin():
        admit_guest(db, settings, ip)
        members = {}
        for role, kind, name in (
            ("customer", "customer", "Guest customer"),
            ("neighbour", "customer", "Another customer"),
            ("support", "support", "Guest support agent"),
        ):
            members[role] = User(
                email=f"{role}-{group}@demo.invalid",
                name=name,
                role=kind,
                password_hash=_DUMMY_HASH,
                demo_group=group,
                demo_expires_at=expires,
            )
            db.add(members[role])
        db.flush()
        orders = {
            "own": add_order(db, members["customer"], "shipped", 3),
            "other": add_order(db, members["neighbour"], "processing", 6),
        }
        token, csrf = start_session(
            db, members["customer"], request.cookies.get(COOKIE_NAME, ""), session_hours(settings)
        )
        audit_session(db, members["customer"].id, "auth.login", members["customer"].id)
    return members["customer"], token, csrf, orders


def guest_orders(engine, principal: Principal) -> dict:
    with Session(engine) as db:
        rows = db.execute(
            select(Order.reference, User.email)
            .join(User, User.id == Order.customer_id)
            .where(User.demo_group == principal.demo_group)
        ).all()
    return {
        "own": next((r.reference for r in rows if r.email.startswith("customer-")), None),
        "other": next((r.reference for r in rows if r.email.startswith("neighbour-")), None),
    }


def switch_view(request: Request, principal: Principal, role: str) -> tuple[User, str, str]:
    """Rotate the session to the same group's customer or support agent."""
    require_hosted_demo(request)
    if principal.demo_group is None:
        raise HTTPException(403, "Only demo guests can switch views")
    prefix = {"customer": "customer-", "support": "support-"}[role]
    settings = request.app.state.settings
    with Session(request.app.state.database, expire_on_commit=False) as db, db.begin():
        target = db.scalar(
            select(User).where(
                User.demo_group == principal.demo_group,
                User.email == f"{prefix}{principal.demo_group}@demo.invalid",
                User.demo_expires_at > utcnow(),
            )
        )
        if target is None:
            raise HTTPException(404, "Demo session expired")
        token, csrf = start_session(
            db, target, request.cookies.get(COOKIE_NAME, ""), session_hours(settings)
        )
        audit_session(db, target.id, "auth.login", target.id)
    return target, token, csrf


def purge_expired_guests(engine, purge_conversation, batch=50) -> int:
    """Delete expired guest groups. purge_conversation(conversation_id) -> bool."""
    from app.models import Conversation

    with Session(engine) as db:
        groups = list(
            db.scalars(
                select(User.demo_group)
                .where(User.demo_group.is_not(None))
                .group_by(User.demo_group)
                .having(func.max(User.demo_expires_at) < utcnow())
                .limit(batch)
            )
        )
    deleted = 0
    for group in groups:
        with Session(engine) as db:
            members = list(db.scalars(select(User.id).where(User.demo_group == group)))
            conversations = list(
                db.scalars(select(Conversation.id).where(Conversation.customer_id.in_(members)))
            )
        # A conversation busy with a run keeps its whole group until the next pass.
        if not all(purge_conversation(id) for id in conversations):
            continue
        with Session(engine) as db, db.begin():
            if db.scalar(
                select(func.count(Conversation.id)).where(Conversation.customer_id.in_(members))
            ):
                continue  # A new conversation appeared after the scan.
            db.execute(delete(Order).where(Order.customer_id.in_(members)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(members)))
            db.execute(delete(RequestTrace).where(RequestTrace.actor_id.in_(members)))
            db.execute(delete(User).where(User.id.in_(members)))
        deleted += 1
    return deleted
