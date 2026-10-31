"""Confirmed synthetic tickets. Database writes, confirmation and replay share a transaction."""

from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conversations import authorize
from app.models import Conversation, Order, Ticket, TicketConfirmation, TicketDraft, utcnow
from app.schemas import TicketDetails, TicketDraftView, TicketView
from app.telemetry import audit_session, bind


def lock_conversation(db, customer, conversation_id):
    authorize(db, customer, conversation_id)
    bind(conversation_id=conversation_id)
    # Every draft/confirmation writer locks the same row; no provider call holds this lock.
    db.execute(select(Conversation).where(Conversation.id == conversation_id).with_for_update())


def validate_order(db, customer, payload):
    reference = payload.get("order_reference")
    if reference and not db.scalar(
        select(Order.id).where(Order.reference == reference, Order.customer_id == customer.id)
    ):
        raise HTTPException(404, "Order unavailable")


def draft_view(db, draft):
    ticket = db.scalar(select(Ticket).where(Ticket.draft_id == draft.id))
    return TicketDraftView.model_validate(draft).model_copy(
        update={"ticket": TicketView.model_validate(ticket) if ticket else None}
    )


def prepare_draft(engine, customer, conversation_id, body):
    payload = TicketDetails.model_validate(body.model_dump(exclude={"request_id"})).model_dump(
        mode="json"
    )
    with Session(engine) as db, db.begin():
        lock_conversation(db, customer, conversation_id)
        existing = db.scalar(
            select(TicketDraft).where(
                TicketDraft.conversation_id == conversation_id,
                TicketDraft.request_id == str(body.request_id),
            )
        )
        if existing:
            if existing.payload != payload:
                raise HTTPException(
                    409, "Request ID was already used with different ticket details"
                )
            return draft_view(db, existing)
        validate_order(db, customer, payload)
        for old in db.scalars(
            select(TicketDraft).where(
                TicketDraft.conversation_id == conversation_id, TicketDraft.status == "pending"
            )
        ):
            old.status = "superseded"
        db.flush()
        draft = TicketDraft(
            id=str(uuid4()),
            conversation_id=conversation_id,
            request_id=str(body.request_id),
            payload=payload,
            status="pending",
            expires_at=utcnow() + timedelta(hours=24),
        )
        db.add(draft)
        audit_session(db, customer.id, "ticket.draft_saved", draft.id, conversation_id)
        db.flush()
        return draft_view(db, draft)


def latest_draft(engine, customer, conversation_id):
    with Session(engine) as db:
        authorize(db, customer, conversation_id)
        bind(conversation_id=conversation_id)
        draft = db.scalar(
            select(TicketDraft)
            .where(TicketDraft.conversation_id == conversation_id)
            .order_by(TicketDraft.created_at.desc(), TicketDraft.id)
            .limit(1)
        )
        return draft_view(db, draft) if draft else None


def create_ticket(engine, customer, conversation_id, confirmed_draft_reference, request_id):
    """Called only by the explicit confirmation endpoint, never by model decisions.

    The immutable draft is the operation key; a unique constraint backs the lock.
    Returning success happens only after the transaction context commits.
    """
    with Session(engine) as db, db.begin():
        lock_conversation(db, customer, conversation_id)
        receipt = db.get(TicketConfirmation, (conversation_id, request_id))
        if receipt and receipt.draft_id != confirmed_draft_reference:
            raise HTTPException(409, "Confirmation request ID already used for another draft")
        draft = db.scalar(
            select(TicketDraft).where(
                TicketDraft.id == confirmed_draft_reference,
                TicketDraft.conversation_id == conversation_id,
            )
        )
        if not draft:
            raise HTTPException(404, "Ticket draft unavailable")
        ticket = db.scalar(select(Ticket).where(Ticket.draft_id == draft.id))
        if not receipt:
            db.add(
                TicketConfirmation(
                    conversation_id=conversation_id, request_id=request_id, draft_id=draft.id
                )
            )
        if ticket:
            return TicketView.model_validate(ticket)
        if draft.status != "pending" or draft.expires_at <= utcnow():
            raise HTTPException(
                409, "Draft expired or replaced. Review a new draft before confirming."
            )
        validate_order(db, customer, draft.payload)
        ticket = Ticket(
            id=str(uuid4()),
            conversation_id=conversation_id,
            draft_id=draft.id,
            customer_id=customer.id,
            request_id=request_id,
            payload=draft.payload,
            status="open",
        )
        db.add(ticket)
        audit_session(db, customer.id, "ticket.created", ticket.id, conversation_id)
        draft.status, draft.confirmed_at = "confirmed", utcnow()
        db.flush()
        return TicketView.model_validate(ticket)


def list_tickets(engine, customer, conversation_id):
    with Session(engine) as db:
        authorize(db, customer, conversation_id)
        bind(conversation_id=conversation_id)
        return [
            TicketView.model_validate(ticket)
            for ticket in db.scalars(
                select(Ticket)
                .where(Ticket.conversation_id == conversation_id)
                .order_by(Ticket.created_at, Ticket.id)
            )
        ]
