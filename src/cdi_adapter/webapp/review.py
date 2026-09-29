"""S8 - human review: turn held facts into decisions.

The reviewer sees, for each fact:
  page image  →  highlighted source bbox  →  OCR span  →  extracted fact  →
  proposed code  →  confidence  →  rule findings  →  accept / correct / reject

Decisions write clinical_fact.review_state (+ reviewed_by/at, review_note) and a
fact_provenance row with agent = the reviewer, so the audit trail shows a human
governed the data. FHIR re-projection then asserts the newly-accepted facts.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .. import repo, storage
from ..db import session_scope
from ..logging import get_logger

log = get_logger(__name__)

_MODEL_STACK_HUMAN = {"reviewer": "human"}


def list_open_tasks(patient_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as sess:
        tasks = repo.list_review_tasks(sess, patient_id=patient_id, open_only=True)
        out = []
        for t in tasks:
            fids = [str(x) for x in (t.get("ref_fact_ids") or [])]
            facts = []
            for fid in fids:
                fr = sess.execute(text("SELECT * FROM clinical_fact WHERE id=:i"),
                                  {"i": fid}).mappings().first()
                if fr and fr["review_state"] in ("in_review", "pending"):
                    facts.append({"fact_id": fid, "fact_type": fr["fact_type"],
                                  "text": fr["local_text"],
                                  "code": fr["code"], "code_system": fr["code_system"],
                                  "code_display": fr["code_display"],
                                  "code_status": fr["code_status"],
                                  "value_num": float(fr["value_num"]) if fr["value_num"] is not None else None,
                                  "unit": fr["value_unit_ucum"],
                                  "confidence": float(fr["confidence_overall"] or 0),
                                  "note": fr["review_note"]})
            if facts:
                out.append({"task_id": str(t["id"]), "kind": t["kind"],
                            "document_id": str(t["document_id"]) if t["document_id"] else None,
                            "patient_id": str(t["patient_id"]) if t["patient_id"] else None,
                            "priority": t["priority"],
                            "doc_type": (t.get("payload") or {}).get("doc_type"),
                            "facts": facts})
        return out


def fact_detail(fact_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        fr = sess.execute(text("SELECT * FROM clinical_fact WHERE id=:i"),
                          {"i": fact_id}).mappings().first()
        if not fr:
            raise ValueError("fact not found")
        fr = dict(fr)
        prov = repo.get_fact_provenance(sess, fact_id)
        md = repo.get_medication_detail(sess, fact_id) if fr["fact_type"] == "medication" else None
        blocks = []
        page_no = None
        page_w = page_h = None
        for p in prov:
            for bid in (p.get("ocr_block_ids") or []):
                b = sess.execute(text("SELECT ob.*, dp.page_no, dp.width_px, dp.height_px "
                                      "FROM ocr_block ob JOIN document_page dp ON dp.id=ob.page_id "
                                      "WHERE ob.id=:i"), {"i": str(bid)}).mappings().first()
                if b:
                    blocks.append({"text": b["text"], "bbox": list(b["bbox"]),
                                   "conf": float(b["ocr_conf"]),
                                   "recognition": b.get("recognition")})
                    page_no = b["page_no"]; page_w = b["width_px"]; page_h = b["height_px"]
        doc_id = str(prov[0]["source_doc_id"]) if prov else None
        # recognition v2 evidence: candidates + practitioner link (extracted vs DB name)
        cands, prac = [], None
        sp = sess.begin_nested()
        try:
            from ..recognition.interpret import fact_candidates

            cands = [{"concept": c["normalized_text"], "concept_id": c["concept_id"],
                      "score": float(c["score"]), "level": c["resolved_by_level"],
                      "alias_class": c["alias_class"], "selected": c["is_selected"],
                      "collision": c["collision"],
                      "plausibility": (c.get("evidence") or {}).get("plausibility")}
                     for c in fact_candidates(sess, fact_id)]
            if doc_id:
                d = repo.get_document(sess, doc_id) or {}
                prac = d.get("practitioner_evidence")
            sp.commit()
        except Exception:  # noqa: BLE001 - pre-0005 database
            sp.rollback()
    return {
        "fact_id": fact_id,
        "fact_type": fr["fact_type"],
        "local_text": fr["local_text"],
        "code_system": fr["code_system"], "code": fr["code"],
        "code_display": fr["code_display"], "code_status": fr["code_status"],
        "value_num": float(fr["value_num"]) if fr["value_num"] is not None else None,
        "value_unit_ucum": fr["value_unit_ucum"],
        "value_text": fr["value_text"], "abnormal_flag": fr["abnormal_flag"],
        "clinical_status": fr["clinical_status"], "verification": fr["verification"],
        "confidence_overall": float(fr["confidence_overall"] or 0),
        "review_state": fr["review_state"], "review_note": fr["review_note"],
        "medication_detail": {k: (float(v) if isinstance(v, (int, float)) else v)
                              for k, v in md.items()} if md else None,
        "document_id": doc_id, "page_no": page_no,
        "page_width": page_w, "page_height": page_h,
        "evidence_bbox_union": [list(p["bbox_union"]) for p in prov if p.get("bbox_union")],
        "extracted_text": [p["extracted_text"] for p in prov if p.get("extracted_text")],
        "ocr_blocks": blocks,
        "evidence_state": fr.get("evidence_state"),
        "field_policy": fr.get("field_policy"),
        "decision_trace": fr.get("decision_trace"),
        "candidates": cands,
        "practitioner": prac,
        "page_image_url": (f"api/documents/{doc_id}/pages/{page_no}"
                           if doc_id and page_no else None),
    }


def submit_decision(fact_id: str, action: str, *, reviewer: str = "reviewer",
                    corrections: dict[str, Any] | None = None,
                    note: str | None = None) -> dict[str, Any]:
    action = action.lower()
    with session_scope() as sess:
        fr = sess.execute(text("SELECT * FROM clinical_fact WHERE id=:i"),
                          {"i": fact_id}).mappings().first()
        if not fr:
            raise ValueError("fact not found")
        prov0 = repo.get_fact_provenance(sess, fact_id)
        src_doc = str(prov0[0]["source_doc_id"]) if prov0 else None
        page_id = str(prov0[0]["page_id"]) if prov0 and prov0[0].get("page_id") else None

        if action == "accept":
            repo.set_fact_review(sess, fact_id, review_state="clinician_confirmed",
                                 reviewed_by=reviewer, reviewed_at_now=True,
                                 review_note=note or "confirmed as extracted")
        elif action == "reject":
            repo.set_fact_review(sess, fact_id, review_state="rejected",
                                 reviewed_by=reviewer, reviewed_at_now=True,
                                 review_note=note or "rejected by reviewer")
            sess.execute(text("UPDATE clinical_fact SET is_current=false, clinical_status='entered-in-error' WHERE id=:i"),
                         {"i": fact_id})
        elif action == "correct":
            repo.apply_fact_correction(sess, fact_id, corrections or {}, reviewer, note)
        else:
            raise ValueError(f"unknown action {action!r}")

        if src_doc:
            repo.insert_fact_provenance(
                sess, fact_id=fact_id, source_doc_id=src_doc, page_id=page_id,
                ocr_block_ids=[], bbox_union=None,
                extracted_text=f"human {action}" + (f": {note}" if note else ""),
                pipeline_run_ids=[], model_stack=_MODEL_STACK_HUMAN, agent=reviewer,
            )
        # close a review_task once none of its facts remain in_review
        tasks = sess.execute(
            text("SELECT * FROM review_task WHERE :f = ANY(ref_fact_ids) AND status IN ('queued','in_progress')"),
            {"f": fact_id},
        ).mappings().all()
        for t in tasks:
            remaining = sess.execute(
                text("SELECT count(*) FROM clinical_fact "
                     "WHERE id = ANY(:ids) AND review_state IN ('pending','in_review')"),
                {"ids": [str(x) for x in t["ref_fact_ids"]]},
            ).scalar_one()
            if remaining == 0:
                repo.close_review_task(sess, t["id"], "done")
        # immutable ledger: the human decision is the governed value from now on
        fr2 = dict(sess.execute(text("SELECT * FROM clinical_fact WHERE id=:i"),
                                {"i": fact_id}).mappings().first())
        try:
            from ..validate.service import record_verified

            md2 = repo.get_medication_detail(sess, fact_id) if fr2["fact_type"] == "medication" else None
            method = {"accept": "clinician_confirmed", "correct": "corrected",
                      "reject": "rejected"}[action]
            trace = list(fr2.get("decision_trace") or []) + [
                {"tier": "human", "action": action, "reviewer": reviewer, "note": note,
                 "corrections": corrections or {}}]
            ev = type("Ev", (), {"evidence_state": fr2.get("evidence_state"),
                                 "observation_ids": [], "winning_candidate_ids": []})()
            with sess.begin_nested():     # savepoint: a ledger error cannot undo the decision
                record_verified(sess, fr2, md2, ev, method=method, confidence=None,
                                policy_id=fr2.get("field_policy"), trace=trace,
                                document_id=src_doc, reviewer_id=reviewer)
        except Exception as exc:  # noqa: BLE001 - pre-0005 database
            log.warning("verified_fact_skipped", fact_id=fact_id, error=str(exc)[:200])
        repo.write_audit(sess, actor=reviewer, action=action, entity="clinical_fact",
                         entity_id=fact_id, patient_id=str(fr["patient_id"]),
                         detail={"corrections": corrections or {}, "note": note})
    log.info("review_decision", fact_id=fact_id, action=action, reviewer=reviewer)
    if src_doc:
        _after_decision(src_doc, str(fr["patient_id"]))
    return {"fact_id": fact_id, "action": action, "ok": True}


def _after_decision(document_id: str, patient_id: str) -> None:
    """Keep the normalised rx_* tables in step and queue a FHIR rebuild for the agent."""
    try:
        from ..persist.normalized import enqueue_fhir, sync_document

        with session_scope() as sess:
            sync_document(sess, document_id)
            enqueue_fhir(sess, patient_id=patient_id, document_id=document_id,
                         reason="review_decision")
    except Exception as exc:  # noqa: BLE001
        log.warning("post_decision_sync_skipped", document_id=document_id, error=str(exc)[:200])
