"""unique log chunk per (run, line)

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX uq_run_events_line ON run_events (run_id, (data->>'line')) "
               "WHERE kind IN ('stdout','stderr')")


def downgrade() -> None:
    op.execute("DROP INDEX uq_run_events_line")
