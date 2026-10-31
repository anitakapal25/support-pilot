"""A permanent automation pause and a single-owner human support queue."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checkpoints import checkpoint_connection
from app.models import (
    AgentRun,
    Conversation,
    Handoff,
    Message,
    Order,
    Ticket,
    ToolExecution,
    User,
    utcnow,
)
from app.schemas import ChatResponse, HandoffView, MessageView, RequestHandoff, TicketView
from app.telemetry import audit_connection, audit_session, bind


def handoff_view(row):
    if isinstance(row, dict):
        return HandoffView(
            **{
                **row,
                **{
                    key: str(row[key]) if row[key] else None
                    for key in ("id", "conversation_id", "assigned_to")
                },
            }
        )
    return HandoffView.model_validate(row)


def paused_response(request_id, handoff):
    view = handoff_view(handoff)
    text = {
        "queued": "Your conversation is queued for human support. Automated answers are paused.",
        "assigned": "A support agent has claimed this conversation. Automated answers are paused.",
        "resolved": "Human support has resolved this conversation. Start a new chat for automated help.",
    }[view.status]
    return ChatResponse(request_id=request_id, outcome="handoff", message=text, handoff=view)


def create_handoff(conn, customer, conversation_id, arguments):
    """Caller owns the transaction. The conversation row also fences AI publication."""
    body = RequestHandoff.model_validate(arguments)
    if customer.role != "customer":
        raise HTTPException(403, "Customer account required")
    conversation = conn.execute(
        "SELECT id FROM conversations WHERE id=%s AND customer_id=%s FOR UPDATE",
        (conversation_id, customer.id),
    ).fetchone()
    if not conversation:
        raise HTTPException(404, "Conversation unavailable")
    existing = conn.execute(
        "SELECT * FROM handoffs WHERE conversation_id=%s", (conversation_id,)
    ).fetchone()
    if existing:
        return existing  # Resolved conversations never reopen automation or a second queue entry.
    bind(conversation_id=conversation_id)
    row = conn.execute(
        "INSERT INTO handoffs(id,conversation_id,reason,summary,status,created_at) "
        "VALUES (%s,%s,%s,%s,'queued',now()) RETURNING *",
        (str(uuid4()), conversation_id, body.reason, body.summary),
    ).fetchone()
    audit_connection(conn, customer.id, "handoff.queued", row["id"], conversation_id)
    return row


def request_handoff(engine, customer, conversation_id, body):
    with checkpoint_connection(engine) as conn, conn.transaction():
        return handoff_view(create_handoff(conn, customer, conversation_id, body.model_dump()))


def require_support(actor):
    if actor.role != "support":
        raise HTTPException(403, "Support account required")


def locked_handoff(db, actor, handoff_id):
    require_support(actor)
    # Always lock conversation first, like queue creation, sequence allocation, and AI finalization.
    conversation_id = db.scalar(select(Handoff.conversation_id).where(Handoff.id == handoff_id))
    if not conversation_id:
        raise HTTPException(404, "Handoff unavailable")
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    handoff = db.scalar(
        select(Handoff).where(Handoff.id == handoff_id).execution_options(populate_existing=True)
    )
    if not conversation or not handoff:
        raise HTTPException(404, "Handoff unavailable")
    bind(conversation_id=conversation.id)
    return conversation, handoff


def queue(engine, actor, status="queued"):
    require_support(actor)
    with Session(engine) as db:
        return [
            handoff_view(row)
            for row in db.scalars(
                select(Handoff)
                .where(Handoff.status == status)
                .order_by(Handoff.created_at, Handoff.id)
                .limit(100)
            )
        ]


def claim(engine, actor, handoff_id):
    with Session(engine) as db, db.begin():
        _, handoff = locked_handoff(db, actor, handoff_id)
        if handoff.status == "assigned" and handoff.assigned_to == actor.id:
            return handoff_view(handoff)
        if handoff.status != "queued":
            raise HTTPException(409, "Handoff is already claimed or resolved")
        handoff.status, handoff.assigned_to, handoff.assigned_at = "assigned", actor.id, utcnow()
        audit_session(db, actor.id, "handoff.claimed", handoff.id, handoff.conversation_id)
        db.flush()
        return handoff_view(handoff)


def resolve(engine, actor, handoff_id):
    with Session(engine) as db, db.begin():
        _, handoff = locked_handoff(db, actor, handoff_id)
        if handoff.assigned_to != actor.id or handoff.status == "queued":
            raise HTTPException(
                409, "Only the assigned support agent can resolve this conversation"
            )
        if handoff.status != "resolved":
            handoff.status, handoff.resolved_at = "resolved", utcnow()
            audit_session(db, actor.id, "handoff.resolved", handoff.id, handoff.conversation_id)
            db.flush()
        return handoff_view(handoff)


def message_view(db, message):
    author = db.get(User, message.author_id) if message.author_id else None
    return MessageView.model_validate(message, from_attributes=True).model_copy(
        update={"author_name": author.name if author else None}
    )


def reply(engine, actor, handoff_id, body):
    with Session(engine) as db, db.begin():
        conversation, handoff = locked_handoff(db, actor, handoff_id)
        if handoff.assigned_to != actor.id:
            raise HTTPException(409, "Claim the conversation before replying")
        existing = db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.human_request_id == str(body.request_id),
            )
        )
        if existing:
            if existing.author_id != actor.id or existing.content != body.message:
                raise HTTPException(409, "Reply request ID was already used with different text")
            return message_view(db, existing)
        if handoff.status != "assigned":
            raise HTTPException(409, "This handoff is resolved")
        message = Message(
            id=str(uuid4()),
            conversation_id=conversation.id,
            run_id=None,
            author_id=actor.id,
            human_request_id=str(body.request_id),
            sequence=conversation.next_sequence,
            role="support",
            content=body.message,
        )
        conversation.next_sequence += 1
        db.add(message)
        audit_session(db, actor.id, "support.replied", message.id, conversation.id)
        db.flush()
        return message_view(db, message)


def support_detail(engine, actor, handoff_id):
    require_support(actor)
    with Session(engine) as db:
        handoff = db.get(Handoff, handoff_id)
        if not handoff:
            raise HTTPException(404, "Handoff unavailable")
        conversation = db.get(Conversation, handoff.conversation_id)
        customer = db.get(User, conversation.customer_id)
        messages = [
            message_view(db, row)
            for row in db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
        ]
        orders = [
            {
                "reference": order.reference,
                "status": order.status,
                "estimated_delivery": order.estimated_delivery,
            }
            for order in db.scalars(
                select(Order)
                .where(Order.customer_id == customer.id)
                .order_by(Order.reference)
                .limit(50)
            )
        ]
        attempts = [
            {
                "run_id": tool.run_id,
                "tool": tool.tool_name,
                "arguments": tool.arguments,
                "result": tool.result,
            }
            for tool in db.scalars(
                select(ToolExecution)
                .join(AgentRun, AgentRun.id == ToolExecution.run_id)
                .where(AgentRun.conversation_id == conversation.id)
                .order_by(AgentRun.user_sequence, ToolExecution.tool_name)
            )
        ]
        tickets = [
            TicketView.model_validate(row)
            for row in db.scalars(
                select(Ticket)
                .where(Ticket.conversation_id == conversation.id)
                .order_by(Ticket.created_at)
            )
        ]
        runs = [
            {"id": run.id, "status": run.status, "error_code": run.error_code}
            for run in db.scalars(
                select(AgentRun)
                .where(AgentRun.conversation_id == conversation.id)
                .order_by(AgentRun.user_sequence)
            )
        ]
        return {
            "handoff": handoff_view(handoff),
            "customer": {"name": customer.name, "email": customer.email},
            "messages": messages,
            "orders": orders,
            "attempted_actions": attempts,
            "runs": runs,
            "tickets": tickets,
        }
