from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from .. import repo
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from . import rules as R

log = get_logger(__name__)


@dataclass
class ValidateResult:
    document_id: str
    auto_accepted: int
    in_review: int
    conflicts: int
    blockers: int


def _calibrate(conf: float, *, partial: bool, n_warn: int, n_block: int) -> float:
    """Placeholder calibration: honest downgrades until a fitted model exists.
    (roadmap: isotonic regression per doc_type x fact_type on adjudicated data)."""
    c = float(conf or 0.0)
    if partial:
        c -= settings.gate_partial_penalty
    if n_block:
        c = min(c, 0.5)
    elif n_warn:
        c = min(c, 0.9)
    return round(max(0.0, min(1.0, c)), 3)


def _dominant_kind(findings_by_fact: dict[str, list[R.Finding]]) -> str:
    codes = [c for fs in findings_by_fact.values() for (_s, c, _m) in fs]
    if any(c.startswith("med-") for c in codes):
        return "dose_check"
    if any(c in ("value-out-of-range", "date-in-future", "date-implausible") for c in codes):
        return "low_confidence"
    if any(c == "unmapped-concept" for c in codes):
        return "unmapped_terminology"
    if any(c in ("no-provenance", "weak-evidence") for c in codes):
        return "low_confidence"
    return "low_confidence"


def validate_document(document_id: str | UUID) -> ValidateResult:
    document_id = str(document_id)
    auto = review = conflicts = blockers_total = 0

    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise ValueError(f"document {document_id} not found")
        cls = repo.get_doc_classification(sess, document_id)
        facts = repo.list_clinical_facts(sess, document_id=document_id)
        run_id = repo.start_pipeline_run(sess, document_id=document_id, stage="validate",
                                         model_name="rules-v1")

        ext = sess.execute(
            text("SELECT payload FROM extraction WHERE document_id = :d "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"d": document_id},
        ).scalar_one_or_none()
        partial = bool(isinstance(ext, dict) and ext.get("_partial"))

        enc_cache: dict[str, dict[str, Any]] = {}

        def enc_of(fid_row: dict[str, Any]) -> dict[str, Any] | None:
            eid = fid_row.get("encounter_id")
            if not eid:
                return None
            eid = str(eid)
            if eid not in enc_cache:
                r = sess.execute(text("SELECT * FROM encounter WHERE id = :i"),
                                 {"i": eid}).mappings().first()
                enc_cache[eid] = dict(r) if r else {}
            return enc_cache[eid]

        held: dict[str, list[R.Finding]] = {}
        held_fact_payload: list[dict[str, Any]] = []

        for f in facts:
            fid = str(f["id"])
            md = repo.get_medication_detail(sess, fid) if f["fact_type"] == "medication" else None
            prov = repo.get_fact_provenance(sess, fid)
            enc = enc_of(f)

            findings: list[R.Finding] = []
            findings += R.check_value_range(f)
            findings += R.check_unit_present(f)
            findings += R.check_dates(f, enc)
            findings += R.check_evidence(f, prov)
            findings += R.check_terminology(f, settings.gate_local_only_review_types)
            if f["fact_type"] == "medication":
                findings += R.check_medication(f, md)

            n_block = sum(1 for s, _c, _m in findings if s == "blocker")
            n_warn = sum(1 for s, _c, _m in findings if s == "warn")
            blockers_total += n_block

            conf = _calibrate(f.get("confidence_overall"), partial=partial,
                              n_warn=n_warn, n_block=n_block)

            note = "; ".join(f"[{s}] {m}" for s, _c, m in findings) or None
            must_review = (
                n_block > 0
                or partial
                or conf < settings.gate_review_floor
                or (settings.gate_medication_always_review and f["fact_type"] == "medication"
                    and any(c.startswith("med-") for _s, c, _m in findings))
            )

            if must_review:
                state = "in_review"
                review += 1
                held[fid] = findings
                held_fact_payload.append({
                    "fact_id": fid, "fact_type": f["fact_type"],
                    "text": f["local_text"], "confidence": conf,
                    "findings": [{"severity": s, "code": c, "message": m} for s, c, m in findings],
                })
            elif conf >= settings.gate_auto_accept_conf and not findings:
                state = "auto_accepted"
                auto += 1
            elif conf >= settings.gate_audit_conf and n_block == 0:
                state = "auto_accepted"
                auto += 1
                if random.random() < settings.audit_sample_rate:
                    repo.create_review_task(
                        sess, kind="low_confidence", patient_id=str(f["patient_id"]),
                        document_id=document_id, ref_fact_ids=[fid], priority=5,
                        payload={"reason": "audit sample", "confidence": conf},
                    )
            else:
                state = "in_review"
                review += 1
                held[fid] = findings
                held_fact_payload.append({
                    "fact_id": fid, "fact_type": f["fact_type"],
                    "text": f["local_text"], "confidence": conf, "findings": [],
                })

            repo.set_fact_review(sess, fid, review_state=state,
                                 confidence_overall=conf, review_note=note)

            # cross-document duplicates / contradictions
            if f["fact_type"] in ("condition", "lab_result", "procedure", "medication") and f.get("code"):
                for other in repo.find_similar_current_facts(
                    sess, patient_id=str(f["patient_id"]), fact_type=f["fact_type"],
                    code=str(f["code"]), exclude_id=fid,
                ):
                    same_day = _same_day(f.get("effective_time"), other.get("effective_time"))
                    va, vb = f.get("value_num"), other.get("value_num")
                    if va is not None and vb is not None and same_day and abs(float(va) - float(vb)) > 1e-6:
                        repo.insert_fact_conflict(
                            sess, patient_id=str(f["patient_id"]), fact_a=fid,
                            fact_b=str(other["id"]), conflict_type="value_mismatch",
                            evidence_state="CONTRADICTED", severity="blocker")
                        conflicts += 1
                        if state != "in_review":
                            repo.set_fact_review(sess, fid, review_state="in_review",
                                                 review_note="cross-document value conflict")
                            review += 1
                            auto = max(0, auto - 1)
                    elif same_day and (va == vb or (not va and not vb)):
                        repo.insert_fact_conflict(
                            sess, patient_id=str(f["patient_id"]), fact_a=fid,
                            fact_b=str(other["id"]), conflict_type="duplicate",
                            evidence_state="SUPPORTED", severity="info",
                            auto_resolution="keep_a")
                        conflicts += 1

        if held:
            repo.create_review_task(
                sess, kind=_dominant_kind(held),
                patient_id=str(facts[0]["patient_id"]) if facts else None,
                document_id=document_id,
                ref_fact_ids=list(held.keys()), priority=2,
                payload={"doc_type": (cls or {}).get("doc_type"),
                         "held": held_fact_payload, "partial_extraction": partial},
            )

        repo.finish_pipeline_run(sess, run_id, status="ok",
                                 metrics={"auto_accepted": auto, "in_review": review,
                                          "conflicts": conflicts, "blockers": blockers_total,
                                          "partial_extraction": partial})
        repo.set_document_status(sess, document_id, "validated")
        repo.write_audit(sess, actor="validate-svc", action="update", entity="clinical_fact",
                         entity_id=document_id,
                         patient_id=str(facts[0]["patient_id"]) if facts else None,
                         detail={"auto_accepted": auto, "in_review": review,
                                 "conflicts": conflicts})

    log.info("validated", document_id=document_id, auto_accepted=auto, in_review=review,
             conflicts=conflicts, blockers=blockers_total, partial=partial)
    return ValidateResult(document_id, auto, review, conflicts, blockers_total)


def _same_day(a: Any, b: Any) -> bool:
    if not isinstance(a, datetime) or not isinstance(b, datetime):
        return False
    return a.date() == b.date()
