"""0011: the patient's name is confirmed by a person.

A handwritten name is never final on its own. ``name_read`` keeps what the reader read; ``patient_name`` is what is shown and
grouped on (the read name until someone confirms or corrects it); ``name_confirmed_by`` / ``name_confirmed_at`` say who did.
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE source_document
  ADD COLUMN IF NOT EXISTS name_read          text,
  ADD COLUMN IF NOT EXISTS name_confirmed_by  text,
  ADD COLUMN IF NOT EXISTS name_confirmed_at  timestamptz;
UPDATE source_document SET name_read = patient_name WHERE name_read IS NULL AND patient_name IS NOT NULL;
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE source_document DROP COLUMN IF EXISTS name_confirmed_at, DROP COLUMN IF EXISTS name_confirmed_by,
  DROP COLUMN IF EXISTS name_read;
""")
