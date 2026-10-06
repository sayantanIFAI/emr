"""0007: LS-S3 ignored-file record + OUT-S1 (what the page says about patient / doctor / clinic,
lab-test preparation and context, and which models read it).

* ``listener_ignored``: files the listener skipped, with the reason (a skipped file is never silent).
* ``rx_prescription``: patient contact + doctor + clinic + follow-up as READ from the page, each
  value's check status in ``field_status`` (value / status / reason), and the versions that produced
  the row (``provenance``).
* ``rx_investigation_preparation`` / ``rx_investigation_context``: preparation written on the page
  and the diagnosis / complaint each test sits with (``same_line`` or ``same_page``), with evidence.
* ``extraction``: prompt version, engine versions and the model's raw answer, kept beside the parsed
  payload (the reading is never overwritten).
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS listener_ignored (
  id            bigserial PRIMARY KEY,
  connector     text NOT NULL,
  name          text NOT NULL,
  reason        text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  times_seen    integer NOT NULL DEFAULT 1,
  UNIQUE (connector, name, reason)
);

ALTER TABLE rx_prescription
  ADD COLUMN IF NOT EXISTS patient_name        text,
  ADD COLUMN IF NOT EXISTS patient_age_text    text,
  ADD COLUMN IF NOT EXISTS patient_dob         text,
  ADD COLUMN IF NOT EXISTS patient_sex         text,
  ADD COLUMN IF NOT EXISTS patient_mrn         text,
  ADD COLUMN IF NOT EXISTS patient_phone       text,
  ADD COLUMN IF NOT EXISTS patient_address     text,
  ADD COLUMN IF NOT EXISTS patient_abha_id     text,
  ADD COLUMN IF NOT EXISTS doctor_name         text,
  ADD COLUMN IF NOT EXISTS doctor_reg_no       text,
  ADD COLUMN IF NOT EXISTS doctor_department   text,
  ADD COLUMN IF NOT EXISTS doctor_designation  text,
  ADD COLUMN IF NOT EXISTS doctor_qualification text,
  ADD COLUMN IF NOT EXISTS clinic_name         text,
  ADD COLUMN IF NOT EXISTS clinic_address      text,
  ADD COLUMN IF NOT EXISTS clinic_phone        text,
  ADD COLUMN IF NOT EXISTS stamp_present       boolean,
  ADD COLUMN IF NOT EXISTS signature_present   boolean,
  ADD COLUMN IF NOT EXISTS follow_up_kind      text,
  ADD COLUMN IF NOT EXISTS follow_up_value     numeric,
  ADD COLUMN IF NOT EXISTS follow_up_unit      text,
  ADD COLUMN IF NOT EXISTS follow_up_date      text,
  ADD COLUMN IF NOT EXISTS field_status        jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS needs_check_count   integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS provenance          jsonb NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS rx_investigation_preparation (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prescription_id uuid NOT NULL REFERENCES rx_prescription(id) ON DELETE CASCADE,
  line_no         int NOT NULL,
  prep_type       text,
  value_num       numeric,
  unit            text,
  prep_text       text NOT NULL,
  applies_to      jsonb NOT NULL DEFAULT '[]',
  status          text NOT NULL,
  reason          text,
  evidence        jsonb NOT NULL DEFAULT '[]',
  UNIQUE (prescription_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_rx_prep_rx ON rx_investigation_preparation (prescription_id);

CREATE TABLE IF NOT EXISTS rx_investigation_context (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prescription_id uuid NOT NULL REFERENCES rx_prescription(id) ON DELETE CASCADE,
  order_id        uuid REFERENCES rx_investigation_order(id) ON DELETE CASCADE,
  test_text       text NOT NULL,
  context_text    text NOT NULL,
  context_kind    text NOT NULL,
  relation        text NOT NULL CHECK (relation IN ('same_line', 'same_page')),
  quote           text,
  UNIQUE (prescription_id, test_text, context_text, context_kind)
);
CREATE INDEX IF NOT EXISTS ix_rx_ctx_rx ON rx_investigation_context (prescription_id);

ALTER TABLE extraction
  ADD COLUMN IF NOT EXISTS prompt_version  text,
  ADD COLUMN IF NOT EXISTS engine_versions jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS raw_answer      text;
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE extraction DROP COLUMN IF EXISTS raw_answer, DROP COLUMN IF EXISTS engine_versions,
  DROP COLUMN IF EXISTS prompt_version;
DROP TABLE IF EXISTS rx_investigation_context;
DROP TABLE IF EXISTS rx_investigation_preparation;
ALTER TABLE rx_prescription
  DROP COLUMN IF EXISTS provenance, DROP COLUMN IF EXISTS needs_check_count, DROP COLUMN IF EXISTS field_status,
  DROP COLUMN IF EXISTS follow_up_date, DROP COLUMN IF EXISTS follow_up_unit, DROP COLUMN IF EXISTS follow_up_value,
  DROP COLUMN IF EXISTS follow_up_kind, DROP COLUMN IF EXISTS signature_present, DROP COLUMN IF EXISTS stamp_present,
  DROP COLUMN IF EXISTS clinic_phone, DROP COLUMN IF EXISTS clinic_address, DROP COLUMN IF EXISTS clinic_name,
  DROP COLUMN IF EXISTS doctor_qualification, DROP COLUMN IF EXISTS doctor_designation,
  DROP COLUMN IF EXISTS doctor_department, DROP COLUMN IF EXISTS doctor_reg_no, DROP COLUMN IF EXISTS doctor_name,
  DROP COLUMN IF EXISTS patient_abha_id, DROP COLUMN IF EXISTS patient_address, DROP COLUMN IF EXISTS patient_phone,
  DROP COLUMN IF EXISTS patient_mrn, DROP COLUMN IF EXISTS patient_sex, DROP COLUMN IF EXISTS patient_dob,
  DROP COLUMN IF EXISTS patient_age_text, DROP COLUMN IF EXISTS patient_name;
DROP TABLE IF EXISTS listener_ignored;
""")
