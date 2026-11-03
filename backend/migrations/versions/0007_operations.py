"""Add shared admission controls without changing existing business tables."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_counters",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operational_counters_expires_at", "operational_counters", ["expires_at"])
    op.create_table(
        "pilot_controls",
        sa.Column("key", sa.String(20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("admission_percent", sa.Integer(), nullable=False),
    )
    op.create_table(
        "usage_reservations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("reserved_micro_usd", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_reservations_month", "usage_reservations", ["month"])
    op.create_index("ix_usage_reservations_created_at", "usage_reservations", ["created_at"])


def downgrade():
    op.drop_table("usage_reservations")
    op.drop_table("pilot_controls")
    op.drop_table("operational_counters")
