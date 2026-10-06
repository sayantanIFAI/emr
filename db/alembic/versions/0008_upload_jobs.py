"""0008: OUT-S3 - one upload gives one result, even if repeated or interrupted.

* ``upload_idempotency``: the Idempotency-Key of a Send -> its job id, so the same Send after a web app
  restart still answers with the same job (it used to live only in the process).
* ``source_document.upload_job_id``: which Send a document belongs to, so a job can be shown again
  after a restart from the database alone.
* ``source_document.resume_attempts``: how many times an interrupted document was picked up again; after
  the cap it is parked as an error (a poison document cannot loop forever).
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS upload_idempotency (
  idem_key   text PRIMARY KEY,
  job_id     text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE source_document
  ADD COLUMN IF NOT EXISTS upload_job_id   text,
  ADD COLUMN IF NOT EXISTS resume_attempts integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_source_document_upload_job ON source_document (upload_job_id);
""")


def downgrade() -> None:
    op.execute("""
DROP INDEX IF EXISTS ix_source_document_upload_job;
ALTER TABLE source_document DROP COLUMN IF EXISTS resume_attempts, DROP COLUMN IF EXISTS upload_job_id;
DROP TABLE IF EXISTS upload_idempotency;
""")
