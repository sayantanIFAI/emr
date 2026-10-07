"""0010: who a prescription is for (token + mobile) and the lab-test name mapping table.

* ``source_document.token_no / phone / patient_name``: the token and mobile number the front desk typed with the
  upload, and the patient's name as read from the page (set once the document is read). The screen groups by patient
  name + mobile number and finds a patient by typing part of the mobile number.
* ``lab_test_alias``: many written names -> ONE standard test ("sr creatinine", "creatine", "RFT" -> Creatinine). The
  rows are seeded by the application (``extract/lab_mapping.py``) and can be added to or switched off from the screen;
  a row is never deleted (``enabled`` = false), so a seed row switched off does not come back.
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE source_document
  ADD COLUMN IF NOT EXISTS token_no     text,
  ADD COLUMN IF NOT EXISTS phone        text,
  ADD COLUMN IF NOT EXISTS patient_name text;
CREATE INDEX IF NOT EXISTS ix_source_document_phone ON source_document (phone text_pattern_ops);
CREATE INDEX IF NOT EXISTS ix_source_document_phone_token ON source_document (phone, token_no);

CREATE TABLE IF NOT EXISTS lab_test_alias (
  alias_key   text PRIMARY KEY,                -- the written name, lower case, punctuation as single spaces
  alias       text NOT NULL,                   -- as the person typed it
  canonical   text NOT NULL,                   -- the one standard test it stands for
  loinc       text,                            -- its standard code, when known
  note        text,
  source      text NOT NULL DEFAULT 'user',    -- 'seed' | 'user'
  enabled     boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_lab_test_alias_canonical ON lab_test_alias (canonical);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE IF EXISTS lab_test_alias;
DROP INDEX IF EXISTS ix_source_document_phone_token;
DROP INDEX IF EXISTS ix_source_document_phone;
ALTER TABLE source_document DROP COLUMN IF EXISTS patient_name, DROP COLUMN IF EXISTS phone, DROP COLUMN IF EXISTS token_no;
""")
