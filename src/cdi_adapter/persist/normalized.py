"""Normalised prescription store (E17-S1/S2) + FHIR outbox (E17-S3).

After extraction -> binding -> validation, every current fact of a document is written to
the relational ``rx_*`` tables (one row per medication / investigation / diagnosis /
complaint / vital / advice), linked back to its ``clinical_fact`` so review state and
provenance stay one join away. ``v_rx_governed_medication`` exposes only governed rows.

The sync is idempotent: child rows of the document's prescription are rebuilt each time.
It never builds FHIR itself - it queues the document in ``fhir_outbox`` for the builder
agent (agents/fhir_builder.py).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .. import repo
from ..logging import get_logger

log = get_logger(__name__)

_CHILD = ("rx_medication_order", "rx_investigation_order", "rx_diagnosis", "rx_complaint",
          "rx_vital", "rx_advice")


def _selected_concept(sess: Any, fact_id: Any) -> str | None:
    r = sess.execute(text(
        "SELECT concept_id FROM interpretation_candidate WHERE fact_id = :f AND is_selected "
        "LIMIT 1"), {"f": str(fact_id)}).first()
    return r[0] if r else None


def sync_document(sess: Any, document_id: str) -> dict[str, int]:
    doc = repo.get_document(sess, document_id)
    if not doc:
        raise ValueError(f"document {document_id} not found")
    facts = repo.list_clinical_facts(sess, document_id=document_id)
    facts = [f for f in facts if f.get("is_current", True)]
    cls = repo.get_doc_classification(sess, document_id) or {}
    enc_id = next((f.get("encounter_id") for f in facts if f.get("encounter_id")), None)
    enc = None
    if enc_id:
        enc = sess.execute(text("SELECT * FROM encounter WHERE id = :i"),
                           {"i": str(enc_id)}).mappings().first()
    pid = next((f.get("patient_id") for f in facts if f.get("patient_id")), None)
    rx_id = sess.execute(text(
        """
        INSERT INTO rx_prescription (document_id, patient_id, encounter_id, practitioner_id,
                                     doc_type, encounter_date, synced_at)
        VALUES (:d, :p, :e, :pr, :t, :ed, now())
        ON CONFLICT (document_id) DO UPDATE SET
          patient_id = EXCLUDED.patient_id, encounter_id = EXCLUDED.encounter_id,
          practitioner_id = EXCLUDED.practitioner_id, doc_type = EXCLUDED.doc_type,
          encounter_date = EXCLUDED.encounter_date, synced_at = now()
        RETURNING id
        """),
        {"d": document_id, "p": str(pid) if pid else None, "e": str(enc_id) if enc_id else None,
         "pr": str(doc["practitioner_id"]) if doc.get("practitioner_id") else None,
         "t": cls.get("doc_type"), "ed": (enc or {}).get("period_start")}).scalar_one()
    for t in _CHILD:
        sess.execute(text(f"DELETE FROM {t} WHERE prescription_id = :r"), {"r": str(rx_id)})

    n: dict[str, int] = {t: 0 for t in _CHILD}
    line = 0
    for f in facts:
        ft, fid = f["fact_type"], str(f["id"])
        base = {"r": str(rx_id), "f": fid, "cs": f.get("code_system"), "c": f.get("code")}
        if ft == "medication":
            md = repo.get_medication_detail(sess, fid) or {}
            line += 1
            sess.execute(text(
                """
                INSERT INTO rx_medication_order
                  (prescription_id, fact_id, line_no, drug_text, concept_id, code_system, code,
                   strength_num, strength_unit, form, dose_num, dose_unit, route,
                   frequency_code, frequency_per_day, duration_days, prn, instructions)
                VALUES (:r, :f, :ln, :dt, :cid, :cs, :c, :sn, :su, :fo, :dn, :du, :ro,
                        :fc, :fpd, :dd, :prn, :ins)
                """),
                {**base, "ln": line, "dt": md.get("drug_text") or f.get("local_text") or "",
                 "cid": _selected_concept(sess, fid), "sn": md.get("strength_num"),
                 "su": md.get("strength_unit"), "fo": md.get("form"), "dn": md.get("dose_num"),
                 "du": md.get("dose_unit_ucum"), "ro": md.get("route"),
                 "fc": md.get("frequency_code"), "fpd": md.get("frequency_per_day"),
                 "dd": md.get("duration_days"), "prn": md.get("prn"),
                 "ins": md.get("instructions")})
            n["rx_medication_order"] += 1
        elif ft == "investigation_order":
            cid = _selected_concept(sess, fid)
            kind = None
            if cid:
                a = sess.execute(text("SELECT attrs FROM kb_concept WHERE id = :i"),
                                 {"i": cid}).scalar_one_or_none() or {}
                kind = a.get("kind")
            sess.execute(text(
                "INSERT INTO rx_investigation_order (prescription_id, fact_id, order_text, "
                "concept_id, code_system, code, kind) VALUES (:r, :f, :t, :cid, :cs, :c, :k)"),
                {**base, "t": f.get("local_text") or "", "cid": cid, "k": kind})
            n["rx_investigation_order"] += 1
        elif ft == "condition":
            sess.execute(text(
                "INSERT INTO rx_diagnosis (prescription_id, fact_id, diagnosis_text, code_system, "
                "code) VALUES (:r, :f, :t, :cs, :c)"), {**base, "t": f.get("local_text") or ""})
            n["rx_diagnosis"] += 1
        elif ft in ("symptom", "finding"):
            sess.execute(text(
                "INSERT INTO rx_complaint (prescription_id, fact_id, complaint_text, code_system, "
                "code) VALUES (:r, :f, :t, :cs, :c)"), {**base, "t": f.get("local_text") or ""})
            n["rx_complaint"] += 1
        elif ft == "vital_sign":
            sess.execute(text(
                "INSERT INTO rx_vital (prescription_id, fact_id, name, value_num, value_text, "
                "unit, code_system, code) VALUES (:r, :f, :nm, :vn, :vt, :u, :cs, :c)"),
                {**base, "nm": f.get("code_display") or f.get("local_text") or "",
                 "vn": f.get("value_num"), "vt": f.get("value_text"),
                 "u": f.get("value_unit_ucum")})
            n["rx_vital"] += 1
        elif ft == "advice":
            sess.execute(text(
                "INSERT INTO rx_advice (prescription_id, fact_id, advice_text) "
                "VALUES (:r, :f, :t)"),
                {"r": str(rx_id), "f": fid, "t": f.get("value_text") or f.get("local_text") or ""})
            n["rx_advice"] += 1
    log.info("rx_synced", document_id=document_id, **n)
    return n


def enqueue_fhir(sess: Any, *, patient_id: str | None, document_id: str, reason: str) -> None:
    """Queue (or re-arm) the document for the FHIR builder agent. One pending row per doc."""
    if not patient_id:
        return
    sess.execute(text(
        """
        INSERT INTO fhir_outbox (patient_id, document_id, reason)
        VALUES (:p, :d, :r)
        ON CONFLICT (document_id) WHERE status = 'pending'
        DO UPDATE SET reason = EXCLUDED.reason, next_attempt_at = now(), updated_at = now()
        """), {"p": str(patient_id), "d": document_id, "r": reason})
