from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthStatus(BaseModel):
    status: Literal["ok", "ready"]


class OrderStatus(BaseModel):
    """Verified order facts; identity is never part of model-visible arguments."""

    model_config = ConfigDict(extra="forbid")

    order_reference: str = Field(min_length=1, max_length=64)
    status: Literal["processing", "shipped", "delivered", "cancelled"]
    estimated_delivery: datetime | None = None
    checked_at: datetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class SessionView(BaseModel):
    id: str
    role: Literal["customer", "support"]
    name: str
    email: str
    csrf_token: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)


class LookupArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_reference: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    article_id: str
    version_id: str
    title: str
    section: str
    updated_at: datetime
    excerpt: str


class HandoffView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    status: Literal["queued", "assigned", "resolved"]
    reason: str
    summary: str
    assigned_to: str | None
    created_at: datetime
    assigned_at: datetime | None
    resolved_at: datetime | None


class RequestHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: Literal[
        "customer_request", "unresolved_issue", "insufficient_evidence", "dependency_failure"
    ]
    summary: str = Field(min_length=1, max_length=1000)


class SupportReply(ChatRequest):
    request_id: UUID


class ChatResponse(BaseModel):
    request_id: str
    outcome: Literal[
        "verified", "clarification", "unavailable", "unsupported", "error", "policy", "handoff"
    ]
    message: str
    order: OrderStatus | None = None
    citations: list[Citation] = Field(default_factory=list)
    handoff: HandoffView | None = None


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class SubmitMessage(ChatRequest):
    request_id: UUID


class ConversationView(BaseModel):
    id: str
    created_at: datetime


class MessageView(BaseModel):
    id: str
    run_id: str | None
    author_name: str | None = None
    sequence: int
    role: Literal["user", "assistant", "support"]
    content: str
    result: ChatResponse | None
    created_at: datetime


class RunView(BaseModel):
    id: str
    conversation_id: str
    request_id: str
    status: Literal["pending", "running", "retryable", "completed", "failed"]
    attempts: int
    response: ChatResponse | None
    error_code: str | None


class ConversationDetail(ConversationView):
    handoff: HandoffView | None = None
    messages: list[MessageView]
    runs: list[RunView]


class TicketDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=10, max_length=2000)
    order_reference: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$"
    )


class CreateTicketDraft(TicketDetails):
    request_id: UUID


class ConfirmTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    draft_id: str
    status: Literal["open"]
    payload: TicketDetails
    created_at: datetime


class TicketDraftView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: Literal["pending", "superseded", "confirmed"]
    payload: TicketDetails
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    ticket: TicketView | None = None
