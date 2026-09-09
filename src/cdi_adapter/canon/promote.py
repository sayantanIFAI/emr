"""Promote an APPROVED review item into the canonical EMR.

`clinical_fact` rows are AI-proposed staging. A reviewer works a `rv_item`
(one per source document) in the workbasket app; on approve, this module maps
every accepted / corrected element to Layer-1 `cn_*` rows, writes the AI
provenance chain (ai_event -> ai_suggestion -> ai_clinical_verification) and a
`cn_provenance` row per resource, and stamps the audit log.

Idempotent per rv_item: a second call on an already-promoted item is a no-op.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import session_scope
from ..logging import get_logger
from . import repo as cr

log = get_logger(__name__)

_STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mcg|ug|mg|g|ml|iu|units?|%)", re.I)

# doc_type -> encounter class
_ENC_CLASS = {
    "discharge_summary": "IPD", "operative_note": "IPD",
    "radiology_report": "DIAGNOSTIC", "lab_report": "DIAGNOSTIC",
}


def _strength(txt: str) -> tuple[float | None, str | None, str | None]:
    m = _STRENGTH_RE.search(txt or "")
    if not m:
        return None, None, txt or None
    unit = {"ug": "ug", "mcg": "ug", "mg": "mg", "g": "g", "ml": "mL",
            "iu": "[IU]", "unit": "[IU]", "units": "[IU]", "%": "%"}.get(
        m.group(2).lower(), m.group(2).lower())
    clean = (txt[:m.start()] + txt[m.end():]).strip(" -,/") or txt
    return float(m.group(1)), unit, clean


def _final(el: dict[str, Any]) -> dict[str, Any]:
    """Reviewer-corrected value if present, else the AI proposal."""
    corrected = el["decision"] == "correct"
    return {
        "text": (el["reviewer_text"] if corrected and el["reviewer_text"] else el["proposed_text"]),
        "value_num": (el["reviewer_value_num"] if corrected and el["reviewer_value_num"] is not None
                      else el["proposed_value_num"]),
        "value_unit": (el["reviewer_value_unit"] if corrected and el["reviewer_value_unit"]
                       else el["proposed_value_unit"]),
        "freq_text": el["proposed_freq_text"],
        "code_system": el["proposed_code_system"],
        "code": el["proposed_code"],
    }


def promote_review_item(rv_item_id: str, reviewer: str) -> dict[str, Any]:
    with session_scope() as sess:
        item = sess.execute(text("SELECT * FROM rv_item WHERE id = :i"),
                            {"i": rv_item_id}).mappings().first()
        if not item:
            raise KeyError(rv_item_id)
        if item["state"] == "approved" and item["canonical_patient_id"]:
            return {"already": True, "patient_id": str(item["canonical_patient_id"])}

        elements = sess.execute(text("""
            SELECT * FROM rv_item_element WHERE rv_item_id = :i
              AND decision IN ('accept', 'correct')
            ORDER BY fact_type
        """), {"i": rv_item_id}).mappings().all()

        # ---- identity from the legacy tables --------------------------------
        ident = None
        if item["patient_identity_id"]:
            ident = sess.execute(text("SELECT * FROM patient_identity WHERE id = :i"),
                                 {"i": item["patient_identity_id"]}).mappings().first()
        reg = None
        if item["mpi_id"]:
            reg = sess.execute(text("SELECT * FROM patient_registry WHERE patient_id = :m LIMIT 1"),
                               {"m": item["mpi_id"]}).mappings().first()

        name_full = (ident and ident.get("name_full")) or (reg and reg.get("name")) or "UNKNOWN"
        given = name_full.split(" ")[0] if name_full else None
        family = name_full.split(" ")[-1] if name_full and " " in name_full else None
        gender = (ident and ident.get("gender")) or (reg and reg.get("gender"))
        dob = (ident and ident.get("birth_date")) or (reg and reg.get("dob"))

        base_prov = {
            "activity": "promote", "agent_type": "clinician", "agent_id": reviewer,
            "occurred_at": datetime.now(timezone.utc),
            "source_document_id": str(item["source_document_id"]) if item["source_document_id"] else None,
            "model_id": "mlserve/vlm", "model_version": "qwen2.5-vl-7b",
            "prompt_version": "v3", "review_item_id": rv_item_id,
            "signature": f"reviewer:{reviewer}",
        }

        pid = cr.ensure_patient(
            sess, mpi_id=item["mpi_id"], name_full=name_full, given=given, family=family,
            gender=gender, birth_date=dob,
            birth_precision="day" if dob else None, prov=base_prov)

        # identifiers: MPI as MRN, ABHA + ABHA address, mobile, address
        if item["mpi_id"]:
            cr.add_identifier(sess, pid, cr.MRN_SYS, item["mpi_id"], use="usual",
                              assigner="CareFlow Polyclinic")
        abha = (reg and reg.get("abha_id")) or (ident and ident.get("abha_number"))
        if abha:
            cr.add_identifier(sess, pid, cr.ABHA_SYS, abha)
            if "@" in str(abha):
                cr.add_identifier(sess, pid, cr.ABHA_ADDR_SYS, abha)
        if reg and reg.get("mobile"):
            cr.add_contact(sess, pid, "mobile", reg["mobile"])
        if reg and reg.get("address"):
            cr.add_address(sess, pid, reg["address"])

        org_id = cr.ensure_organization(sess, "CareFlow Polyclinic")
        doc_type = item["doc_type"] or "opd_note"
        klass = _ENC_CLASS.get(doc_type, "OPD")

        sdoc = sess.execute(text("SELECT * FROM source_document WHERE id = :i"),
                            {"i": item["source_document_id"]}).mappings().first() if item["source_document_id"] else None
        period_start = (sdoc and (sdoc.get("captured_at") or sdoc.get("ingested_at"))) or datetime.now(timezone.utc)

        enc_id = cr.create_encounter(
            sess, patient_id=pid, klass=klass, period_start=period_start,
            specialty=None, department=None, attending=None, organization_id=org_id,
            reason=item["patient_display"], derived_from=[str(item["source_document_id"])]
            if item["source_document_id"] else [], prov=base_prov)

        if sdoc:
            cr.insert_document(
                sess, patient_id=pid, encounter_id=enc_id, document_type=doc_type,
                title=(sdoc.get("original_filename") or doc_type), mime_type=sdoc.get("mime_type"),
                storage_uri=sdoc.get("object_uri"), sha256=sdoc.get("sha256"),
                ocr_text=None, source_document_id=str(item["source_document_id"]), prov=base_prov)

        # ---- one ai_event for this document -------------------------------
        ai_event_id = str(sess.execute(text("""
            INSERT INTO ai_event
              (patient_id, encounter_id, source_document_id, task, model_id,
               model_version, prompt_version, human_review_status, reviewed_by, reviewed_at)
            VALUES (:p, :e, :sd, 'entity_extraction', 'mlserve/vlm', 'qwen2.5-vl-7b',
                    'v3', 'accepted', :rv, now())
            RETURNING id
        """), {"p": pid, "e": enc_id, "sd": item["source_document_id"], "rv": reviewer}).first()[0])

        counts: dict[str, int] = {}
        obs_for_report: list[str] = []
        report_element: dict[str, Any] | None = None
        procedure_ids: list[str] = []

        def _bump(k: str) -> None:
            counts[k] = counts.get(k, 0) + 1

        def _verify(el: dict[str, Any], kind: str, table: str, rid: str) -> None:
            sug = sess.execute(text("""
                INSERT INTO ai_suggestion
                  (ai_event_id, review_item_id, staging_fact_id, target_kind,
                   proposed_json, evidence, confidence)
                VALUES (:ae, :rv, :sf, :tk, :pj, :ev, :cf) RETURNING id
            """), {"ae": ai_event_id, "rv": rv_item_id, "sf": el["fact_id"], "tk": kind,
                   "pj": _json(el, "proposed"), "ev": _json(el, "evidence"),
                   "cf": None}).first()[0]
            sess.execute(text("""
                INSERT INTO ai_clinical_verification
                  (ai_suggestion_id, review_item_id, reviewer, decision, corrected_json,
                   note, final_resource_table, final_resource_id)
                VALUES (:s, :rv, :who, :dec, :cj, :note, :frt, :frid)
            """), {"s": sug, "rv": rv_item_id, "who": reviewer, "dec": el["decision"],
                   "cj": _json(el, "reviewer") if el["decision"] == "correct" else None,
                   "note": el["note"], "frt": table, "frid": rid})

        prov = dict(base_prov)

        for el in elements:
            ft = el["fact_type"]
            v = _final(el)
            prov["bbox"] = list(el["bbox"]) if el["bbox"] else None
            prov["agent_type"] = "clinician" if el["decision"] == "correct" else "ai"

            if ft == "medication":
                sn, su, clean = _strength(v["text"] or "")
                rid = cr.insert_medication_order(
                    sess, patient_id=pid, encounter_id=enc_id,
                    drug_text=(clean or v["text"] or "medication"),
                    dose_num=v["value_num"], dose_unit=v["value_unit"], route=None,
                    frequency_code=v["freq_text"], frequency_per_day=None,
                    timing_text=v["freq_text"], duration_days=None, instructions=None,
                    prescriber_id=None, strength_num=sn or v["value_num"],
                    strength_unit=su or v["value_unit"], form=None, prov=prov)
                _verify(el, "medication_order", "cn_medication_order", rid)
                _bump("medication_order")

            elif ft == "condition":
                rid = cr.insert_condition(
                    sess, patient_id=pid, encounter_id=enc_id,
                    category="ENCOUNTER_DIAGNOSIS", display=v["text"] or "condition",
                    code_system=v["code_system"], code=v["code"],
                    clinical_status="active", verification="provisional", onset=None, prov=prov)
                _verify(el, "condition", "cn_condition", rid)
                _bump("condition")

            elif ft == "vital_sign":
                rid = cr.insert_observation(
                    sess, patient_id=pid, encounter_id=enc_id, category="vital-sign",
                    display=v["text"] or "vital sign", code_system=v["code_system"],
                    code=v["code"], value_num=v["value_num"], value_unit=v["value_unit"],
                    value_string=None, abnormal_flag=el.get("proposed_code") and None,
                    effective=period_start, prov=prov)
                _verify(el, "observation", "cn_observation", rid)
                _bump("observation")

            elif ft == "lab_result":
                rid = cr.insert_lab_result(
                    sess, patient_id=pid, encounter_id=enc_id,
                    test_name=v["text"] or "analyte", code_system=v["code_system"],
                    code=v["code"], value_num=v["value_num"], value_unit=v["value_unit"],
                    value_string=None, ref_low=None, ref_high=None, ref_text=None,
                    abnormal_flag=None, prov=prov)
                oid = cr.insert_observation(
                    sess, patient_id=pid, encounter_id=enc_id, category="laboratory",
                    display=v["text"] or "analyte", code_system=v["code_system"], code=v["code"],
                    value_num=v["value_num"], value_unit=v["value_unit"], value_string=None,
                    abnormal_flag=None, effective=period_start, prov=prov)
                obs_for_report.append(oid)
                _verify(el, "lab_result", "cn_lab_result", rid)
                _bump("lab_result")

            elif ft == "finding":
                oid = cr.insert_observation(
                    sess, patient_id=pid, encounter_id=enc_id, category="imaging",
                    display=v["text"] or "finding", code_system=v["code_system"], code=v["code"],
                    value_num=None, value_unit=None, value_string=v["text"],
                    abnormal_flag=None, effective=period_start, prov=prov)
                obs_for_report.append(oid)
                _verify(el, "observation", "cn_observation", oid)
                _bump("observation")

            elif ft == "procedure":
                rid = cr.insert_procedure(
                    sess, patient_id=pid, encounter_id=enc_id, display=v["text"] or "procedure",
                    code_system=v["code_system"], code=v["code"], outcome=None, body_site=None,
                    performed=period_start, prov=prov)
                procedure_ids.append(rid)
                _verify(el, "procedure", "cn_procedure", rid)
                _bump("procedure")

            elif ft == "allergy":
                rid = cr.insert_allergy(
                    sess, patient_id=pid, encounter_id=enc_id,
                    substance_display=v["text"] or "allergen", substance_system=v["code_system"],
                    substance_code=v["code"], category="medication", criticality="high", prov=prov)
                _verify(el, "allergy", "cn_allergy", rid)
                _bump("allergy")

            elif ft == "diagnostic_report":
                report_element = el

        # a lab / imaging document rolls its observations into one DiagnosticReport
        if doc_type in ("lab_report", "radiology_report") and (obs_for_report or report_element):
            re_v = _final(report_element) if report_element else {"text": None, "code": None, "code_system": None}
            did = cr.insert_diagnostic_report(
                sess, patient_id=pid, encounter_id=enc_id,
                category="RAD" if doc_type == "radiology_report" else "LAB",
                display=(re_v["text"] or ("Imaging report" if doc_type == "radiology_report"
                                          else "Laboratory report")),
                code_system=re_v["code_system"], code=re_v["code"],
                conclusion=re_v["text"], result_obs=obs_for_report, effective=period_start,
                prov=prov)
            if report_element:
                _verify(report_element, "diagnostic_report", "cn_diagnostic_report", did)
            _bump("diagnostic_report")

        cr.audit(sess, actor=reviewer, role="reviewer", patient_id=pid,
                 resource_table="rv_item", resource_id=rv_item_id, action="CREATE",
                 reason="promote approved review item -> canonical EMR")

        sess.execute(text("""
            UPDATE rv_item SET state = 'approved', assignee = COALESCE(assignee, :who),
              completed_at = now(), canonical_patient_id = :cp, encounter_id = :enc
            WHERE id = :i
        """), {"who": reviewer, "cp": pid, "enc": enc_id, "i": rv_item_id})
        sess.execute(text("""INSERT INTO rv_action (rv_item_id, actor, action, detail)
                             VALUES (:i, :a, 'approve', :d)"""),
                     {"i": rv_item_id, "a": reviewer, "d": _dumps({"counts": counts})})

        log.info("promoted", rv_item=rv_item_id, patient=pid, counts=counts)
        return {"patient_id": pid, "encounter_id": enc_id, "counts": counts}


# --- tiny json helpers (psycopg needs a json-adapted str) ------------------
import json  # noqa: E402


def _dumps(o: Any) -> str:
    return json.dumps(o, default=str)


def _json(el: dict[str, Any], which: str) -> str:
    if which == "evidence":
        return _dumps({"bbox": list(el["bbox"]) if el["bbox"] else None,
                       "page_width": el["page_width"], "page_height": el["page_height"]})
    if which == "proposed":
        return _dumps({k[len("proposed_"):]: el[k] for k in el
                       if k.startswith("proposed_") and el[k] is not None})
    return _dumps({k[len("reviewer_"):]: el[k] for k in el
                   if k.startswith("reviewer_") and el[k] is not None})
