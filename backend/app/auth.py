"""Opaque sessions and CSRF protection. The model never supplies a Principal."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import LoginRateLimit, LoginSession, User, utcnow
from app.telemetry import audit_session, bind, emit

COOKIE_NAME = "support_session"
password_hasher = PasswordHasher()
_DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    name: str
    role: str
    csrf_token: str


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in request.app.state.settings.allowed_origins:
        raise HTTPException(403, "Origin not allowed")


def current_principal(request: Request) -> Principal:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Sign in to continue")
    with Session(request.app.state.database) as db:
        row = db.execute(
            select(LoginSession, User)
            .join(User, User.id == LoginSession.user_id)
            .where(LoginSession.token_hash == token_hash(token), LoginSession.expires_at > utcnow())
        ).first()
        if row is None:
            raise HTTPException(401, "Sign in to continue")
        session, user = row
        principal = Principal(user.id, user.email, user.name, user.role, session.csrf_token)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), principal.csrf_token
        ):
            raise HTTPException(403, "Invalid CSRF token")
    bind(actor_id=principal.id)
    emit("auth.authorized", outcome="allowed")
    return principal


def throttle_login(request: Request, email: str) -> None:
    """Atomic PostgreSQL counters work across API replicas; expired buckets are pruned."""
    now = utcnow()
    bucket = int(now.timestamp()) // 900
    ip = request.client.host if request.client else "unknown"
    exceeded = False
    with Session(request.app.state.database) as db, db.begin():
        db.execute(delete(LoginRateLimit).where(LoginRateLimit.expires_at < now))
        for scope, value, limit in (("ip", ip, 40), ("account", email, 8)):
            key = token_hash(f"{scope}:{value}:{bucket}")
            stmt = insert(LoginRateLimit).values(
                key=key, attempts=1, expires_at=now + timedelta(minutes=20)
            )
            count = db.scalar(
                stmt.on_conflict_do_update(
                    index_elements=[LoginRateLimit.key],
                    set_={"attempts": LoginRateLimit.attempts + 1},
                ).returning(LoginRateLimit.attempts)
            )
            exceeded = exceeded or count > limit
    if exceeded:
        raise HTTPException(
            429, "Too many sign-in attempts; try again later", headers={"Retry-After": "900"}
        )


def authenticate(request: Request, email: str, password: str) -> tuple[User, str, str]:
    check_origin(request)
    # Requiring a custom header also protects login requests without a session CSRF token.
    if request.headers.get("x-requested-with") != "support-pilot":
        raise HTTPException(403, "Missing request header")
    throttle_login(request, email)
    with Session(request.app.state.database, expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.email == email))
        try:
            password_hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except VerificationError:
            raise HTTPException(401, "Email or password is incorrect") from None
        if user is None:
            raise HTTPException(401, "Email or password is incorrect")
        old_token = request.cookies.get(COOKIE_NAME, "")
        if old_token:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
        db.execute(delete(LoginSession).where(LoginSession.expires_at < utcnow()))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(
            LoginSession(
                token_hash=token_hash(token),
                user_id=user.id,
                csrf_token=csrf,
                expires_at=utcnow() + timedelta(hours=request.app.state.settings.session_hours),
            )
        )
        bind(actor_id=user.id)
        audit_session(db, user.id, "auth.login", user.id)
        db.commit()
        return user, token, csrf


def current_customer(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.role != "customer":
        raise HTTPException(403, "Customer account required")
    return principal


def current_support(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.role != "support":
        raise HTTPException(403, "Support account required")
    return principal
