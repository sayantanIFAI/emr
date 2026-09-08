"""patient MPI id + identity fields fetched from documents

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE patient_identity
          ADD COLUMN IF NOT EXISTS mpi_id text,
          ADD COLUMN IF NOT EXISTS name_full text,
          ADD COLUMN IF NOT EXISTS age_years int,
          ADD COLUMN IF NOT EXISTS identity_confidence numeric(4,3);

        CREATE SEQUENCE IF NOT EXISTS patient_mpi_seq START 1;

        CREATE UNIQUE INDEX IF NOT EXISTS ux_patient_mpi_id
          ON patient_identity (mpi_id) WHERE mpi_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ux_patient_mpi_id;
        DROP SEQUENCE IF EXISTS patient_mpi_seq;
        ALTER TABLE patient_identity
          DROP COLUMN IF EXISTS mpi_id,
          DROP COLUMN IF EXISTS name_full,
          DROP COLUMN IF EXISTS age_years,
          DROP COLUMN IF EXISTS identity_confidence;
        """
    )
