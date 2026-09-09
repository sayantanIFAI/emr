"""patient_registry — clinic's own patient master (lookup by id / abha / mobile)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_registry (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          patient_id  text,                       -- CareFlow id (CFP-...) once known
          name        text NOT NULL,
          mobile      text NOT NULL,
          dob         date,
          gender      text,
          address     text,
          abha_id     text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        -- "primary key" per the clinic: mobile + name + dob
        CREATE UNIQUE INDEX IF NOT EXISTS ux_reg_mobile_name_dob
          ON patient_registry (mobile, lower(name), COALESCE(dob, DATE '1900-01-01'));
        CREATE INDEX IF NOT EXISTS ix_reg_patient_id ON patient_registry (patient_id);
        CREATE INDEX IF NOT EXISTS ix_reg_abha       ON patient_registry (abha_id);
        CREATE INDEX IF NOT EXISTS ix_reg_mobile     ON patient_registry (mobile);

        INSERT INTO patient_registry (patient_id, name, mobile, dob, gender, address, abha_id) VALUES
          ('CFP-2026-000901','Ramesh Kumar',    '9830011234','1968-04-12','M','12 M G Road, Kolkata 700007',            '14-1111-2222-3333'),
          ('CFP-2026-000902','Sunita Devi',     '9830022345','1975-11-30','F','5B Lake Gardens, Kolkata 700045',        '14-2222-3333-4444'),
          ('CFP-2026-000903','Anil Chatterjee', '9830033456','1959-01-05','M','Flat 4, Salt Lake Sector V, Kolkata 700091','14-3333-4444-5555'),
          ('CFP-2026-000904','Priya Sharma',    '9830044567','1990-07-22','F','221 Park Street, Kolkata 700016',        NULL)
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_registry CASCADE;")
