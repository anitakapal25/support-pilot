from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import handoffs
from app.auth import (
    COOKIE_NAME,
    Principal,
    authenticate,
    current_customer,
    current_principal,
    current_support,
    token_hash,
)
from app.conversations import (
    DurableTurn,
    conversation_detail,
    create_conversation,
    list_conversations,
    read_run,
)
from app.models import LoginSession
from app.orders import OrderUnavailable, SyntheticCommerceAdapter
from app.schemas import (
    ConfirmTicket,
    ConversationDetail,
    ConversationView,
    CreateConversation,
    CreateTicketDraft,
    HandoffView,
    LoginRequest,
    MessageView,
    OrderStatus,
    RequestHandoff,
    RunView,
    SessionView,
    SubmitMessage,
    SupportReply,
    TicketDraftView,
    TicketView,
)
from app.telemetry import audit_session
from app.tickets import create_ticket, latest_draft, list_tickets, prepare_draft

router = APIRouter(prefix="/api")
Customer = Annotated[Principal, Depends(current_customer)]
SignedIn = Annotated[Principal, Depends(current_principal)]
Support = Annotated[Principal, Depends(current_support)]


@router.get("/config")
def public_config(request: Request) -> dict:
    return {"demo": request.app.state.settings.agent_provider == "demo"}


@router.post("/auth/login", response_model=SessionView)
def login(body: LoginRequest, request: Request, response: Response) -> SessionView:
    user, token, csrf = authenticate(request, body.email, body.password)
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return SessionView(
        id=user.id, role=user.role, name=user.name, email=user.email, csrf_token=csrf
    )


@router.get("/auth/me", response_model=SessionView)
def me(customer: SignedIn) -> SessionView:
    return SessionView(
        id=customer.id,
        role=customer.role,
        name=customer.name,
        email=customer.email,
        csrf_token=customer.csrf_token,
    )


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, customer: SignedIn) -> None:
    with Session(request.app.state.database) as db, db.begin():
        audit_session(db, customer.id, "auth.logout", customer.id)
        db.execute(
            delete(LoginSession).where(
                LoginSession.token_hash == token_hash(request.cookies[COOKIE_NAME])
            )
        )
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.get("/orders/{reference}", response_model=OrderStatus)
def order_status(
    request: Request,
    customer: Customer,
    reference: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")],
) -> OrderStatus:
    try:
        return SyntheticCommerceAdapter(request.app.state.database).lookup_order(
            customer, reference
        )
    except OrderUnavailable:
        raise HTTPException(404, "Order unavailable") from None


@router.post("/chat", deprecated=True)
def old_chat(customer: Customer):
    raise HTTPException(410, "Use /api/conversations/{id}/messages with a stable request_id")


@router.post("/conversations", response_model=ConversationView)
def new_conversation(body: CreateConversation, request: Request, customer: Customer):
    return create_conversation(request.app.state.database, customer, str(body.request_id))


@router.get("/conversations", response_model=list[ConversationView])
def conversations(request: Request, customer: Customer):
    return list_conversations(request.app.state.database, customer)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def history(conversation_id: UUID, request: Request, customer: Customer):
    return conversation_detail(request.app.state.database, customer, str(conversation_id))


@router.get("/conversations/{conversation_id}/runs/{request_id}", response_model=RunView)
def run_status(conversation_id: UUID, request_id: UUID, request: Request, customer: Customer):
    return read_run(request.app.state.database, customer, str(conversation_id), str(request_id))


@router.post("/conversations/{conversation_id}/messages", response_model=RunView)
def send_message(
    conversation_id: UUID,
    body: SubmitMessage,
    request: Request,
    response: Response,
    customer: Customer,
):
    result = DurableTurn(
        request.app.state.database, customer, request.app.state.provider, request.app.state.settings
    ).submit(
        str(conversation_id),
        str(body.request_id),
        body.message,
    )
    if result.status in ("pending", "running"):
        response.status_code = 202
        response.headers["Retry-After"] = "1"
    elif result.status in ("retryable", "failed"):
        response.status_code = 503
    return result


@router.get("/conversations/{conversation_id}/sources/{chunk_id}")
def source(conversation_id: UUID, chunk_id: UUID, request: Request, customer: Customer):
    from app.retrieval import citation_source

    return citation_source(
        request.app.state.database, customer, str(conversation_id), str(chunk_id)
    )


@router.post("/conversations/{conversation_id}/ticket-drafts", response_model=TicketDraftView)
def new_ticket_draft(
    conversation_id: UUID, body: CreateTicketDraft, request: Request, customer: Customer
):
    return prepare_draft(request.app.state.database, customer, str(conversation_id), body)


@router.get("/conversations/{conversation_id}/ticket-draft", response_model=TicketDraftView | None)
def current_ticket_draft(conversation_id: UUID, request: Request, customer: Customer):
    return latest_draft(request.app.state.database, customer, str(conversation_id))


@router.post(
    "/conversations/{conversation_id}/ticket-drafts/{draft_id}/confirm", response_model=TicketView
)
def confirm_ticket(
    conversation_id: UUID, draft_id: UUID, body: ConfirmTicket, request: Request, customer: Customer
):
    return create_ticket(
        request.app.state.database,
        customer,
        str(conversation_id),
        str(draft_id),
        str(body.request_id),
    )


@router.get("/conversations/{conversation_id}/tickets", response_model=list[TicketView])
def conversation_tickets(conversation_id: UUID, request: Request, customer: Customer):
    return list_tickets(request.app.state.database, customer, str(conversation_id))


@router.post("/conversations/{conversation_id}/handoff", response_model=HandoffView)
def ask_human(conversation_id: UUID, body: RequestHandoff, request: Request, customer: Customer):
    return handoffs.request_handoff(
        request.app.state.database, customer, str(conversation_id), body
    )


@router.get("/support/handoffs", response_model=list[HandoffView])
def support_queue(
    request: Request, actor: Support, status: Literal["queued", "assigned", "resolved"] = "queued"
):
    return handoffs.queue(request.app.state.database, actor, status)


@router.get("/support/handoffs/{handoff_id}")
def support_conversation(handoff_id: UUID, request: Request, actor: Support):
    return handoffs.support_detail(request.app.state.database, actor, str(handoff_id))


@router.post("/support/handoffs/{handoff_id}/claim", response_model=HandoffView)
def claim_handoff(handoff_id: UUID, request: Request, actor: Support):
    return handoffs.claim(request.app.state.database, actor, str(handoff_id))


@router.post("/support/handoffs/{handoff_id}/resolve", response_model=HandoffView)
def resolve_handoff(handoff_id: UUID, request: Request, actor: Support):
    return handoffs.resolve(request.app.state.database, actor, str(handoff_id))


@router.post("/support/handoffs/{handoff_id}/replies", response_model=MessageView)
def support_reply(handoff_id: UUID, body: SupportReply, request: Request, actor: Support):
    return handoffs.reply(request.app.state.database, actor, str(handoff_id), body)
