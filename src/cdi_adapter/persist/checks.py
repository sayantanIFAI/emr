"""OUT-S1: what the page says about the patient, the doctor and clinic, the follow-up, each lab test's
preparation and context, written to the relational tables with every value's check status.

The values are the ones the rules of ``extract/fields.py`` judge (right format, literally on the page);
a value the rules doubt is stored WITH ``needs_check`` and the reason, never as a clean value. It is
rebuilt from the saved extraction and OCR blocks on every sync, so running it twice gives the same rows
(no duplicates), and it runs inside the caller's transaction: a failure leaves none of it behind.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from ..extract import fields as F
from ..output.json_connector import _doc_date, gather
from ..provenance import engine_versions, prompt_version

_PATIENT = {"name": "patient_name", "age_text": "patient_age_text", "dob": "patient_dob",
            "sex": "patient_sex", "mrn": "patient_mrn", "phone": "patient_phone",
            "address": "patient_address", "abha_id": "patient_abha_id"}
_DOCTOR = {"name": "doctor_name", "reg_no": "doctor_reg_no", "department": "doctor_department",
           "designation": "doctor_designation", "qualification": "doctor_qualification"}
_CLINIC = {"name": "clinic_name", "address": "clinic_address", "phone": "clinic_phone"}


def _text(v: Any) -> str | None:
    return None if v is None else str(v)


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def collect(checks: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """``(column values, field_status)`` from the checked fields."""
    cols: dict[str, Any] = {}
    status: dict[str, dict[str, Any]] = {}

    def put(path: str, col: str, c: dict[str, Any], cast: Any = _text) -> None:
        cols[col] = cast(c["value"])
        status[path] = {"status": c["status"], "reason": c["reason"]}

    for k, col in _PATIENT.items():
        put(f"patient.{k}", col, checks["patient"][k])
    for k, col in _DOCTOR.items():
        put(f"doctor.{k}", col, checks["doctor"][k])
    for k, col in _CLINIC.items():
        put(f"doctor.clinic.{k}", col, checks["doctor"]["clinic"][k])
    put("doctor.stamp_present", "stamp_present", checks["doctor"]["stamp_present"], _bool)
    put("doctor.signature_present", "signature_present", checks["doctor"]["signature_present"], _bool)
    fu = checks["follow_up"]
    d = fu.get("detail") or {}
    cols.update(follow_up_text=_text(fu["value"]), follow_up_kind=d.get("kind"),
                follow_up_value=d.get("interval_value"), follow_up_unit=d.get("interval_unit"),
                follow_up_date=_text(d.get("date")))
    status["follow_up"] = {"status": fu["status"], "reason": fu["reason"]}
    return cols, status


def sync_checks(sess: Any, document_id: str, rx_id: Any) -> dict[str, int]:
    inp = gather(sess, document_id)
    if inp is None:
        raise ValueError(f"document {document_id} not found")
    checks = F.build_checks(inp.payload or {}, inp.blocks, _doc_date(inp.document))
    cols, status = collect(checks)
    n_check = sum(1 for s in status.values() if s["status"] == F.NEEDS_CHECK) + sum(
        1 for p in checks["preparation"] if p["status"] == F.NEEDS_CHECK)
    ext = sess.execute(text(
        "SELECT id, schema_version, prompt_version, engine_versions FROM extraction "
        "WHERE document_id = :d ORDER BY created_at DESC LIMIT 1"), {"d": document_id}).mappings().first()
    provenance = {
        "extraction_id": str(ext["id"]) if ext else None,
        "schema_version": ext["schema_version"] if ext else None,
        "prompt_version": (ext["prompt_version"] if ext else None) or prompt_version(),
        "engines": (ext["engine_versions"] if ext else None) or engine_versions(),
        "rules": "extract/fields.py",
    }
    sets = ", ".join(f"{c} = :{c}" for c in cols)
    sess.execute(text(
        f"UPDATE rx_prescription SET {sets}, field_status = CAST(:fs AS jsonb), "
        "needs_check_count = :nc, provenance = CAST(:pv AS jsonb) WHERE id = :r"),
        {**cols, "fs": json.dumps(status), "nc": n_check, "pv": json.dumps(provenance, default=str),
         "r": str(rx_id)})

    sess.execute(text("DELETE FROM rx_investigation_preparation WHERE prescription_id = :r"), {"r": str(rx_id)})
    sess.execute(text("DELETE FROM rx_investigation_context WHERE prescription_id = :r"), {"r": str(rx_id)})
    for i, p in enumerate(checks["preparation"], start=1):
        sess.execute(text(
            "INSERT INTO rx_investigation_preparation (prescription_id, line_no, prep_type, value_num, unit, "
            "prep_text, applies_to, status, reason, evidence) VALUES (:r, :i, :t, :v, :u, :x, "
            "CAST(:a AS jsonb), :s, :why, CAST(:e AS jsonb))"),
            {"r": str(rx_id), "i": i, "t": p.get("type"), "v": p.get("value"), "u": p.get("unit"),
             "x": p["text"], "a": json.dumps(p.get("applies_to") or []), "s": p["status"],
             "why": p.get("reason"), "e": json.dumps(p.get("evidence") or [])})
    orders = {r[1]: r[0] for r in sess.execute(text(
        "SELECT id, order_text FROM rx_investigation_order WHERE prescription_id = :r"), {"r": str(rx_id)})}
    n_ctx = 0
    for entry in checks["context"]:
        for link in entry["context"]:
            sess.execute(text(
                "INSERT INTO rx_investigation_context (prescription_id, order_id, test_text, context_text, "
                "context_kind, relation, quote) VALUES (:r, :o, :t, :c, :k, :rel, :q) "
                "ON CONFLICT (prescription_id, test_text, context_text, context_kind) DO NOTHING"),
                {"r": str(rx_id), "o": str(orders[entry["test"]]) if entry["test"] in orders else None,
                 "t": entry["test"], "c": link["text"], "k": link["kind"], "rel": link["relation"],
                 "q": link.get("quote")})
            n_ctx += 1
    return {"preparation": len(checks["preparation"]), "context": n_ctx, "needs_check": n_check}
