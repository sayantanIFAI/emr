"""0009: LS-S9 - the listener's heartbeat, so health and alerts can say when it last polled well."""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS listener_heartbeat (
  connector       text PRIMARY KEY,
  worker          text,
  last_poll_at    timestamptz,
  last_poll_ok_at timestamptz,
  last_error      text,
  stopped_reason  text,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS listener_heartbeat;")
