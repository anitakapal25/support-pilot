"""Immutable ticket drafts and confirmed durable tickets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ticket_drafts",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_draft_request"),
        sa.UniqueConstraint("id", "conversation_id", name="uq_draft_conversation"),
        sa.CheckConstraint("status IN ('pending','superseded','confirmed')", name="draft_status"),
    )
    op.create_index(
        "uq_pending_ticket_draft",
        "ticket_drafts",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status='pending'"),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("draft_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", name="uq_ticket_draft"),
        sa.UniqueConstraint("conversation_id", "request_id", name="uq_ticket_request"),
        sa.ForeignKeyConstraint(
            ["draft_id", "conversation_id"],
            ["ticket_drafts.id", "ticket_drafts.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status='open'", name="ticket_status"),
    )
    op.create_index("ix_tickets_conversation_id", "tickets", ["conversation_id"])

    op.create_table(
        "ticket_confirmations",
        sa.Column("conversation_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("request_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("draft_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id", "conversation_id"],
            ["ticket_drafts.id", "ticket_drafts.conversation_id"],
            ondelete="CASCADE",
        ),
    )


def downgrade():
    op.drop_table("ticket_confirmations")
    op.drop_table("tickets")
    op.drop_table("ticket_drafts")
