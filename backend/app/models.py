"""Application tables. Schema changes are applied by Alembic, never on API startup."""

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('customer', 'support')", name="user_role"),)
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="customer")


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginRateLimit(Base):
    __tablename__ = "login_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'shipped', 'delivered', 'cancelled')", name="order_status"
        ),
        Index("ix_order_customer_reference", "customer_id", "reference"),
    )
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16))
    estimated_delivery: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_quantity"),)
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_customer_created", "customer_id", "created_at"),)
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id", name="uq_run_request"),
        UniqueConstraint("id", "conversation_id", name="uq_run_conversation"),
        CheckConstraint(
            "status IN ('pending','running','retryable','completed','failed')", name="run_status"
        ),
        Index(
            "uq_unfinished_run",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running','retryable')"),
        ),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    payload_hash: Mapped[str] = mapped_column(String(64))
    user_sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    response: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_message_sequence"),
        UniqueConstraint("run_id", "role", name="uq_message_run_role"),
        CheckConstraint("role IN ('user','assistant','support')", name="message_role"),
        CheckConstraint(
            "(role='support' AND run_id IS NULL AND author_id IS NOT NULL AND human_request_id IS NOT NULL) OR (role IN ('user','assistant') AND run_id IS NOT NULL AND author_id IS NULL AND human_request_id IS NULL)",
            name="message_authorship",
        ),
        UniqueConstraint("conversation_id", "human_request_id", name="uq_human_reply_request"),
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["agent_runs.id", "agent_runs.conversation_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    human_request_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    arguments: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)


class ArticleVersion(Base):
    __tablename__ = "article_versions"
    __table_args__ = (
        UniqueConstraint("article_id", "fingerprint", name="uq_article_fingerprint"),
        Index("uq_active_article", "article_id", unique=True, postgresql_where=text("active")),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    embedding_identity: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "position", name="uq_chunk_position"),
        Index("ix_chunk_search", "search", postgresql_using="gin"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("article_versions.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', section || ' ' || content)", persisted=True)
    )


class TicketDraft(Base):
    __tablename__ = "ticket_drafts"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id", name="uq_draft_request"),
        UniqueConstraint("id", "conversation_id", name="uq_draft_conversation"),
        CheckConstraint("status IN ('pending','superseded','confirmed')", name="draft_status"),
        Index(
            "uq_pending_ticket_draft",
            "conversation_id",
            unique=True,
            postgresql_where=text("status='pending'"),
        ),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_ticket_draft"),
        UniqueConstraint("conversation_id", "request_id", name="uq_ticket_request"),
        ForeignKeyConstraint(
            ["draft_id", "conversation_id"],
            ["ticket_drafts.id", "ticket_drafts.conversation_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("status='open'", name="ticket_status"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    draft_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    request_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TicketConfirmation(Base):
    __tablename__ = "ticket_confirmations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["draft_id", "conversation_id"],
            ["ticket_drafts.id", "ticket_drafts.conversation_id"],
            ondelete="CASCADE",
        ),
    )
    conversation_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    request_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    draft_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Handoff(Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_handoff_conversation"),
        CheckConstraint("status IN ('queued','assigned','resolved')", name="handoff_status"),
        CheckConstraint(
            "(status='queued' AND assigned_to IS NULL AND assigned_at IS NULL AND resolved_at IS NULL) OR (status='assigned' AND assigned_to IS NOT NULL AND assigned_at IS NOT NULL AND resolved_at IS NULL) OR (status='resolved' AND assigned_to IS NOT NULL AND assigned_at IS NOT NULL AND resolved_at IS NOT NULL)",
            name="handoff_assignment",
        ),
        Index("ix_handoff_status_created", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequestTrace(Base):
    __tablename__ = "request_traces"
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    conversation_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    events: Mapped[list] = mapped_column(JSONB)
    dropped_events: Mapped[int] = mapped_column(Integer, default=0)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    actor_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    action: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    conversation_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    trace_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
