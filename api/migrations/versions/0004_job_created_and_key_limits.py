"""job_created_at, and enforceable per-key limits

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("runs", sa.Column("job_created_at", sa.DateTime(timezone=True), nullable=True))
    # Runs already past dispatch have a Job (or had one): treat them as created.
    op.execute("UPDATE runs SET job_created_at = dispatched_at WHERE dispatched_at IS NOT NULL")
    # The per-key limits were never enforced; they are now. Defaults: 600 submissions per minute and 200
    # runs in flight (queued + active) per key.
    op.alter_column("api_keys", "rate_limit_per_min", server_default="600")
    op.alter_column("api_keys", "concurrency_cap", server_default="200")
    op.execute("UPDATE api_keys SET rate_limit_per_min = 600, concurrency_cap = 200")


def downgrade() -> None:
    op.drop_column("runs", "job_created_at")
