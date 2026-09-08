"""initial CDI-Adapter schema + read-model views

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_DB_DIR = Path(__file__).resolve().parents[2]  # .../db


def _run_sql_file(name: str) -> None:
    sql = (_DB_DIR / name).read_text(encoding="utf-8")
    op.execute(sql)


def upgrade() -> None:
    _run_sql_file("schema.sql")
    _run_sql_file("views.sql")


def downgrade() -> None:
    op.execute(
        """
        DROP MATERIALIZED VIEW IF EXISTS v_problem_list, v_medication_list, v_allergy_list;
        DROP VIEW IF EXISTS v_results_grid, v_encounter_timeline;
        DROP TABLE IF EXISTS
          audit_log, abdm_transfer, abdm_consent, abdm_care_context, review_task,
          fhir_bundle, fhir_resource, fact_conflict, fact_provenance, medication_detail,
          clinical_fact, encounter, patient_identity_alias, patient_identity, extraction,
          ocr_block, doc_classification, pipeline_run, document_page, source_document
        CASCADE;
        """
    )
