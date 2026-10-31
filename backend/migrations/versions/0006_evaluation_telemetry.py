"""Redacted request traces and atomic business-action audits."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_traces",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=False)),
        sa.Column("conversation_id", sa.Uuid(as_uuid=False)),
        sa.Column("events", JSONB(), nullable=False),
        sa.Column("dropped_events", sa.Integer(), nullable=False),
    )
    op.create_index("ix_request_traces_started_at", "request_traces", ["started_at"])
    op.create_index("ix_request_traces_conversation_id", "request_traces", ["conversation_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=False)),
        sa.Column("trace_id", sa.Uuid(as_uuid=False)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_conversation_id", "audit_events", ["conversation_id"])
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"])


def downgrade():
    op.drop_table("audit_events")
    op.drop_table("request_traces")
