"""Create identity, session, login throttling, and synthetic commerce tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('customer', 'support')", name="user_role"),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])
    op.create_table(
        "login_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_rate_limits_expires_at", "login_rate_limits", ["expires_at"])
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("reference", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "customer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("estimated_delivery", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('processing', 'shipped', 'delivered', 'cancelled')", name="order_status"
        ),
    )
    op.create_index("ix_order_customer_reference", "orders", ["customer_id", "reference"])
    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "order_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="positive_quantity"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    for table in ("order_items", "orders", "login_rate_limits", "login_sessions", "users"):
        op.drop_table(table)
