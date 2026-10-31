"""Durable handoffs and human-authored messages."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "handoffs",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "assigned_to", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", name="uq_handoff_conversation"),
        sa.CheckConstraint("status IN ('queued','assigned','resolved')", name="handoff_status"),
        sa.CheckConstraint(
            "(status='queued' AND assigned_to IS NULL AND assigned_at IS NULL AND resolved_at IS NULL) OR (status='assigned' AND assigned_to IS NOT NULL AND assigned_at IS NOT NULL AND resolved_at IS NULL) OR (status='resolved' AND assigned_to IS NOT NULL AND assigned_at IS NOT NULL AND resolved_at IS NOT NULL)",
            name="handoff_assignment",
        ),
    )
    op.create_index("ix_handoff_status_created", "handoffs", ["status", "created_at"])
    op.alter_column("messages", "run_id", nullable=True)
    op.add_column(
        "messages",
        sa.Column(
            "author_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", name="fk_message_author", ondelete="RESTRICT"),
        ),
    )
    op.add_column("messages", sa.Column("human_request_id", sa.Uuid(as_uuid=False)))
    op.create_foreign_key(
        "fk_message_conversation",
        "messages",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("message_role", "messages", type_="check")
    op.create_check_constraint("message_role", "messages", "role IN ('user','assistant','support')")
    op.create_check_constraint(
        "message_authorship",
        "messages",
        "(role='support' AND run_id IS NULL AND author_id IS NOT NULL AND human_request_id IS NOT NULL) OR (role IN ('user','assistant') AND run_id IS NOT NULL AND author_id IS NULL AND human_request_id IS NULL)",
    )
    op.create_unique_constraint(
        "uq_human_reply_request", "messages", ["conversation_id", "human_request_id"]
    )


def downgrade():
    # Refuse to destroy human-authored history implicitly.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM messages WHERE role='support')")):
        raise RuntimeError("Archive human messages before downgrading milestone 5")
    op.drop_constraint("uq_human_reply_request", "messages", type_="unique")
    op.drop_constraint("message_authorship", "messages", type_="check")
    op.drop_constraint("message_role", "messages", type_="check")
    op.create_check_constraint("message_role", "messages", "role IN ('user','assistant')")
    op.drop_constraint("fk_message_conversation", "messages", type_="foreignkey")
    op.drop_column("messages", "author_id")
    op.drop_column("messages", "human_request_id")
    op.alter_column("messages", "run_id", nullable=False)
    op.drop_table("handoffs")
