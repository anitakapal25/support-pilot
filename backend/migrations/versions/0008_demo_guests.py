"""Hosted-demo guest groups: nullable columns, so existing accounts are unaffected."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("demo_group", sa.Uuid(as_uuid=False), nullable=True))
    op.add_column("users", sa.Column("demo_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_demo_group", "users", ["demo_group"])
    op.create_index("ix_users_demo_expires_at", "users", ["demo_expires_at"])


def downgrade():
    op.drop_index("ix_users_demo_expires_at", table_name="users")
    op.drop_index("ix_users_demo_group", table_name="users")
    op.drop_column("users", "demo_expires_at")
    op.drop_column("users", "demo_group")
