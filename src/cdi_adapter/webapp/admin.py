"""Admin inspector - is every backend table populated correctly?

Read-only. Groups every table by layer, shows row counts, lets you page recent
rows, and renders the full canonical graph + generated bundle for one patient so
you can eyeball that promotion wrote what it should.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from ..db import session_scope

router = APIRouter(prefix="/api/admin", tags=["admin"])

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

GROUPS: dict[str, list[str]] = {
    "Staging (AI extraction)": [
        "source_document", "document_page", "ocr_block", "doc_classification",
        "extraction", "clinical_fact", "medication_detail", "fact_provenance",
        "encounter", "pipeline_run",
    ],
    "Identity (legacy / MPI)": [
        "patient_identity", "patient_identity_alias", "patient_registry",
    ],
    "Review — workbasket / worklist": [
        "rv_item", "rv_item_element", "rv_action",
    ],
    "Canonical L1 — clinical truth": [
        "cn_patient", "cn_patient_identifier", "cn_patient_contact", "cn_patient_address",
        "cn_patient_related_person", "cn_practitioner", "cn_organization", "cn_location",
        "cn_encounter", "cn_encounter_participant", "cn_clinical_note", "cn_chief_complaint",
        "cn_condition", "cn_allergy", "cn_observation", "cn_medication", "cn_medication_order",
        "cn_medication_administration", "cn_medication_dispense", "cn_lab_order", "cn_specimen",
        "cn_lab_result", "cn_diagnostic_report", "cn_imaging_study", "cn_procedure",
        "cn_care_plan", "cn_goal", "cn_referral", "cn_document", "cn_consent",
        "cn_provenance", "cn_audit_event",
    ],
    "AI provenance layer": ["ai_event", "ai_suggestion", "ai_clinical_verification"],
    "Interoperability (FHIR out)": ["cn_fhir_bundle", "fhir_resource", "fhir_bundle"],
    "Ops / Finance (Layer 2)": [
        "ops_appointment", "ops_ward", "ops_room", "ops_bed", "ops_admission",
        "ops_transfer", "ops_nursing_assessment", "ops_nursing_event", "ops_device",
        "ops_device_observation", "ops_alert", "fin_coverage", "fin_preauth",
        "fin_charge", "fin_invoice", "fin_payment", "fin_claim",
    ],
}


@router.get("/overview")
def overview() -> dict[str, Any]:
    with session_scope() as sess:
        present = {r[0] for r in sess.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).all()}
        out = []
        total = 0
        for group, tables in GROUPS.items():
            rows = []
            for t in tables:
                if t not in present:
                    rows.append({"table": t, "rows": None, "missing": True})
                    continue
                n = sess.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                total += n or 0
                rows.append({"table": t, "rows": int(n or 0)})
            out.append({"group": group, "tables": rows,
                        "group_rows": sum(r["rows"] or 0 for r in rows)})
    return {"total_rows": total, "groups": out}


@router.get("/table/{name}")
def table_rows(name: str, limit: int = 25) -> dict[str, Any]:
    if not _NAME_RE.match(name):
        raise HTTPException(400, "bad table name")
    limit = max(1, min(limit, 200))
    with session_scope() as sess:
        ok = sess.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"),
            {"t": name}).first()
        if not ok:
            raise HTTPException(404, "no such table")
        cols = [r[0] for r in sess.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"),
            {"t": name}).all()]
        order = "created_at" if "created_at" in cols else (
            "recorded_at" if "recorded_at" in cols else (
                "at_time" if "at_time" in cols else None))
        sql = f"SELECT * FROM {name}"
        if order:
            sql += f" ORDER BY {order} DESC"
        sql += " LIMIT :n"
        rows = [dict(r) for r in sess.execute(text(sql), {"n": limit}).mappings().all()]
    return {"table": name, "columns": cols, "count": len(rows),
            "rows": [{k: _s(v) for k, v in r.items()} for r in rows]}


@router.get("/patients")
def patients() -> dict[str, Any]:
    with session_scope() as sess:
        rows = sess.execute(text("""
            SELECT p.id, p.mpi_id, p.name_full, p.gender, p.date_of_birth,
                   (SELECT count(*) FROM cn_patient_identifier i WHERE i.patient_id = p.id) AS ids,
                   (SELECT count(*) FROM cn_encounter e WHERE e.patient_id = p.id) AS encounters,
                   (SELECT count(*) FROM cn_condition c WHERE c.patient_id = p.id) AS conditions,
                   (SELECT count(*) FROM cn_observation o WHERE o.patient_id = p.id) AS observations,
                   (SELECT count(*) FROM cn_medication_order m WHERE m.patient_id = p.id) AS meds,
                   (SELECT count(*) FROM cn_lab_result l WHERE l.patient_id = p.id) AS labs,
                   (SELECT count(*) FROM cn_provenance pr WHERE pr.patient_id = p.id) AS provenance,
                   (SELECT count(*) FROM cn_fhir_bundle b WHERE b.canonical_patient_id = p.id) AS bundles
            FROM cn_patient p ORDER BY p.created_at DESC LIMIT 100
        """)).mappings().all()
    return {"patients": [{k: _s(v) for k, v in dict(r).items()} for r in rows]}


@router.get("/patient/{pid}")
def patient_graph(pid: str) -> dict[str, Any]:
    if not re.match(r"^[0-9a-f-]{36}$", pid):
        raise HTTPException(400, "bad id")
    with session_scope() as sess:
        def q(sql: str) -> list[dict[str, Any]]:
            return [{k: _s(v) for k, v in dict(r).items()}
                    for r in sess.execute(text(sql), {"p": pid}).mappings().all()]

        pat = sess.execute(text("SELECT * FROM cn_patient WHERE id = :p"),
                           {"p": pid}).mappings().first()
        if not pat:
            raise HTTPException(404, "no such patient")
        graph = {
            "patient": {k: _s(v) for k, v in dict(pat).items()},
            "identifiers": q("SELECT system, value, use, assigner FROM cn_patient_identifier WHERE patient_id = :p"),
            "contacts": q("SELECT kind, value FROM cn_patient_contact WHERE patient_id = :p"),
            "encounters": q("SELECT id, klass, status, period_start, department FROM cn_encounter WHERE patient_id = :p"),
            "conditions": q("SELECT category, display, code_system, code, clinical_status FROM cn_condition WHERE patient_id = :p"),
            "observations": q("SELECT category, display, code, value_num, value_unit_ucum, value_string FROM cn_observation WHERE patient_id = :p"),
            "medication_orders": q("SELECT drug_text, dose_num, dose_unit_ucum, frequency_code, duration_days FROM cn_medication_order WHERE patient_id = :p"),
            "lab_results": q("SELECT test_name, code, value_num, value_unit_ucum, abnormal_flag FROM cn_lab_result WHERE patient_id = :p"),
            "diagnostic_reports": q("SELECT category, display, status, conclusion FROM cn_diagnostic_report WHERE patient_id = :p"),
            "procedures": q("SELECT display, code, status FROM cn_procedure WHERE patient_id = :p"),
            "allergies": q("SELECT substance_display, category, criticality FROM cn_allergy WHERE patient_id = :p"),
            "documents": q("SELECT document_type, title, mime_type, source FROM cn_document WHERE patient_id = :p"),
            "provenance": q("SELECT target_table, activity, agent_type, agent_id, model_id, review_item_id FROM cn_provenance WHERE patient_id = :p ORDER BY recorded_at"),
            "ai_events": q("SELECT task, model_id, human_review_status, reviewed_by FROM ai_event WHERE patient_id = :p"),
            "verifications": q("""SELECT v.decision, v.reviewer, v.final_resource_table, s.target_kind
                                  FROM ai_clinical_verification v JOIN ai_suggestion s ON s.id = v.ai_suggestion_id
                                  WHERE s.ai_event_id IN (SELECT id FROM ai_event WHERE patient_id = :p)"""),
            "audit": q("SELECT actor_user, actor_role, action, resource_table, occurred_at FROM cn_audit_event WHERE patient_id = :p ORDER BY occurred_at"),
            "bundles": q("""SELECT artifact, status, validator, validation_ok, ig_package, created_at,
                                   jsonb_array_length(bundle_json->'entry') AS entries
                            FROM cn_fhir_bundle WHERE canonical_patient_id = :p ORDER BY created_at DESC"""),
        }
    return graph


def _s(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, dict)):
        return v
    return str(v)
