"""Workbasket / worklist reviewer app (its own route tree, mounted at /reviewer).

Flow:  extraction -> rv_item lands in the WORKBASKET (state 'open')
       reviewer CLAIMS it -> moves to their WORKLIST (state 'in_progress')
       reviewer works each element (accept / correct / reject)
       APPROVE -> promote to canonical EMR -> build ABDM FHIR bundle -> validate
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import text

from .. import repo
from ..canon.promote import promote_review_item
from ..db import session_scope
from ..fhir.canonical import build_bundle
from ..fhir.validate_abdm import validate as validate_abdm
from ..logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/reviewer", tags=["reviewer"])

FACILITY = "CareFlow Polyclinic"


# --------------------------------------------------------------------------- #
# seed: called when a job reaches state 'review'
# --------------------------------------------------------------------------- #
def seed_from_job(job: Any) -> None:
    """One rv_item per document in the job + one rv_item_element per fact."""
    try:
        with session_scope() as sess:
            for prog in job.docs:
                if not prog.document_id or prog.status == "error":
                    continue
                exists = sess.execute(
                    text("SELECT 1 FROM rv_item WHERE job_id = :j AND source_document_id = :d"),
                    {"j": job.id, "d": prog.document_id}).first()
                if exists:
                    continue
                facts = repo.list_clinical_facts(sess, document_id=prog.document_id)
                facts = [f for f in facts if f["is_current"] and f["review_state"] != "rejected"]
                pages = repo.list_document_pages(sess, prog.document_id)
                pw = pages[0]["width_px"] if pages else None
                ph = pages[0]["height_px"] if pages else None
                prio = "urgent" if any((f["confidence_overall"] or 1) < 0.6 for f in facts) else "routine"
                item_id = sess.execute(text("""
                    INSERT INTO rv_item
                      (kind, job_id, source_document_id, patient_identity_id, mpi_id,
                       patient_display, doc_type, facility, priority, state, n_elements)
                    VALUES ('document', :j, :d, :pi, :mpi, :disp, :dt, :fac, :prio, 'open', :n)
                    RETURNING id
                """), {"j": job.id, "d": prog.document_id,
                       "pi": job.patient_id, "mpi": (job.patient or {}).get("mpi_id"),
                       "disp": (job.patient or {}).get("name") or "Unknown patient",
                       "dt": prog.doc_type, "fac": FACILITY, "prio": prio,
                       "n": len(facts)}).first()[0]
                sess.execute(text(
                    "INSERT INTO rv_action (rv_item_id, actor, action) VALUES (:i, 'system', 'create')"),
                    {"i": item_id})
                for f in facts:
                    prov = repo.get_fact_provenance(sess, f["id"])
                    bbox = next((list(p["bbox_union"]) for p in prov if p.get("bbox_union")), None)
                    md = (repo.get_medication_detail(sess, f["id"])
                          if f["fact_type"] == "medication" else None)
                    val_num = f["value_num"]
                    val_unit = f["value_unit_ucum"]
                    freq = None
                    if md:
                        val_num = md.get("dose_num") or md.get("strength_num") or val_num
                        val_unit = md.get("dose_unit_ucum") or md.get("strength_unit") or val_unit
                        freq = md.get("frequency_code")
                    sess.execute(text("""
                        INSERT INTO rv_item_element
                          (rv_item_id, fact_id, fact_type, proposed_text, proposed_value_num,
                           proposed_value_unit, proposed_code_system, proposed_code,
                           proposed_freq_text, bbox, page_width, page_height)
                        VALUES (:i, :fid, :ft, :ptext, :pnum, :punit, :pcs, :pc, :pfreq,
                                :bbox, :pw, :ph)
                    """), {"i": item_id, "fid": f["id"], "ft": f["fact_type"],
                           "ptext": f["local_text"],
                           "pnum": float(val_num) if val_num is not None else None,
                           "punit": val_unit, "pcs": f["code_system"], "pc": f["code"],
                           "pfreq": freq, "bbox": bbox, "pw": pw, "ph": ph})
        log.info("review_items_seeded", job=job.id, docs=len(job.docs))
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_review_item_failed", job=getattr(job, "id", "?"), error=str(exc)[:200])


# --------------------------------------------------------------------------- #
def _rows(sess, sql: str, **p) -> list[dict[str, Any]]:
    return [dict(r) for r in sess.execute(text(sql), p).mappings().all()]


@router.get("/workbasket")
def workbasket(doc_type: str | None = None, priority: str | None = None) -> dict[str, Any]:
    q = ["state = 'open'"]
    p: dict[str, Any] = {}
    if doc_type:
        q.append("doc_type = :dt"); p["dt"] = doc_type
    if priority:
        q.append("priority = :pr"); p["pr"] = priority
    with session_scope() as sess:
        items = _rows(sess, f"""
            SELECT id, job_id, doc_type, patient_display, mpi_id, facility, priority,
                   n_elements, created_at
            FROM rv_item WHERE {' AND '.join(q)}
            ORDER BY (priority='stat') DESC, (priority='urgent') DESC, created_at ASC
            LIMIT 200
        """, **p)
    return {"count": len(items), "items": items}


@router.get("/worklist")
def worklist(assignee: str) -> dict[str, Any]:
    with session_scope() as sess:
        items = _rows(sess, """
            SELECT id, job_id, doc_type, patient_display, mpi_id, priority, state,
                   n_elements, n_resolved, claimed_at, bundle_status
            FROM rv_item
            WHERE assignee = :a AND state IN ('claimed','in_progress','approved','rejected')
            ORDER BY claimed_at DESC NULLS LAST LIMIT 200
        """, a=assignee)
    return {"count": len(items), "items": items}


@router.post("/items/{item_id}/claim")
def claim(item_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    who = (body or {}).get("assignee") or "reviewer"
    with session_scope() as sess:
        row = sess.execute(text("SELECT state, assignee FROM rv_item WHERE id = :i"),
                           {"i": item_id}).first()
        if not row:
            raise HTTPException(404, "no such item")
        if row[0] != "open" and row[1] != who:
            raise HTTPException(409, f"already {row[0]} by {row[1]}")
        sess.execute(text("""
            UPDATE rv_item SET state = 'in_progress', assignee = :a, claimed_at = now(),
              started_at = COALESCE(started_at, now()) WHERE id = :i
        """), {"a": who, "i": item_id})
        sess.execute(text("INSERT INTO rv_action (rv_item_id, actor, action) VALUES (:i, :a, 'claim')"),
                     {"i": item_id, "a": who})
    return {"ok": True, "state": "in_progress", "assignee": who}


@router.post("/items/{item_id}/release")
def release(item_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    who = (body or {}).get("assignee") or "reviewer"
    with session_scope() as sess:
        sess.execute(text("""
            UPDATE rv_item SET state = 'open', assignee = NULL, claimed_at = NULL
            WHERE id = :i AND state IN ('claimed','in_progress')
        """), {"i": item_id})
        sess.execute(text("INSERT INTO rv_action (rv_item_id, actor, action) VALUES (:i, :a, 'release')"),
                     {"i": item_id, "a": who})
    return {"ok": True, "state": "open"}


@router.get("/items/{item_id}")
def get_item(item_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        item = sess.execute(text("SELECT * FROM rv_item WHERE id = :i"),
                            {"i": item_id}).mappings().first()
        if not item:
            raise HTTPException(404, "no such item")
        els = _rows(sess, "SELECT * FROM rv_item_element WHERE rv_item_id = :i ORDER BY fact_type", i=item_id)
    doc = str(item["source_document_id"])
    return {
        "id": str(item["id"]), "state": item["state"], "assignee": item["assignee"],
        "doc_type": item["doc_type"], "patient_display": item["patient_display"],
        "mpi_id": item["mpi_id"], "priority": item["priority"],
        "page_image_url": f"api/documents/{doc}/pages/1",
        "original_url": f"api/documents/{doc}/original",
        "bundle_status": item["bundle_status"], "validation": item["validation"],
        "canonical_patient_id": str(item["canonical_patient_id"]) if item["canonical_patient_id"] else None,
        "elements": [
            {"id": str(e["id"]), "fact_type": e["fact_type"],
             "proposed_text": e["proposed_text"],
             "proposed_value_num": float(e["proposed_value_num"]) if e["proposed_value_num"] is not None else None,
             "proposed_value_unit": e["proposed_value_unit"],
             "proposed_code": e["proposed_code"], "proposed_freq_text": e["proposed_freq_text"],
             "reviewer_text": e["reviewer_text"],
             "reviewer_value_num": float(e["reviewer_value_num"]) if e["reviewer_value_num"] is not None else None,
             "reviewer_value_unit": e["reviewer_value_unit"],
             "decision": e["decision"], "note": e["note"],
             "bbox": list(e["bbox"]) if e["bbox"] else None,
             "page_width": e["page_width"], "page_height": e["page_height"]}
            for e in els
        ],
    }


@router.post("/items/{item_id}/elements")
def save_elements(item_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    els = (body or {}).get("elements") or []
    who = (body or {}).get("assignee") or "reviewer"
    with session_scope() as sess:
        for e in els:
            sess.execute(text("""
                UPDATE rv_item_element SET
                  decision = :d,
                  reviewer_text = :rt, reviewer_value_num = :rn, reviewer_value_unit = :ru,
                  note = :note, decided_at = now()
                WHERE id = :id AND rv_item_id = :i
            """), {"d": e.get("decision", "pending"), "rt": e.get("reviewer_text"),
                   "rn": e.get("reviewer_value_num"), "ru": e.get("reviewer_value_unit"),
                   "note": e.get("note"), "id": e["id"], "i": item_id})
        n_res = sess.execute(text("""
            SELECT count(*) FROM rv_item_element
            WHERE rv_item_id = :i AND decision <> 'pending'
        """), {"i": item_id}).scalar()
        sess.execute(text("UPDATE rv_item SET n_resolved = :n WHERE id = :i"),
                     {"n": n_res, "i": item_id})
        sess.execute(text("INSERT INTO rv_action (rv_item_id, actor, action, detail) "
                          "VALUES (:i, :a, 'edit', :d)"),
                     {"i": item_id, "a": who, "d": json.dumps({"n": len(els)})})
    return {"ok": True, "n_resolved": n_res}


@router.post("/items/{item_id}/approve")
def approve(item_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    who = (body or {}).get("reviewer") or (body or {}).get("assignee") or "reviewer"
    with session_scope() as sess:
        pend = sess.execute(text("""
            SELECT count(*) FROM rv_item_element WHERE rv_item_id = :i AND decision = 'pending'
        """), {"i": item_id}).scalar()
        st = sess.execute(text("SELECT state FROM rv_item WHERE id = :i"), {"i": item_id}).scalar()
    if st is None:
        raise HTTPException(404, "no such item")
    if pend:
        raise HTTPException(422, f"{pend} element(s) still pending - resolve every row first")

    promo = promote_review_item(item_id, who)

    with session_scope() as sess:
        built = build_bundle(sess, item_id)
        result = validate_abdm(built["bundle"], built["artifact"])
        status = "ready_to_share" if result["ok"] else "draft"
        bid = sess.execute(text("""
            INSERT INTO cn_fhir_bundle
              (canonical_patient_id, encounter_id, rv_item_id, artifact, ig_package,
               bundle_json, status, validator, validation_ok, validation_issues)
            VALUES (:p, (SELECT encounter_id FROM rv_item WHERE id = :i), :i, :art, :ig,
                    :bj, :st, :vr, :ok, :iss)
            RETURNING id
        """), {"p": promo.get("patient_id"), "i": item_id, "art": built["artifact"],
               "ig": built["ig_package"], "bj": json.dumps(built["bundle"]),
               "st": status, "vr": result["validator"], "ok": result["ok"],
               "iss": json.dumps(result["issues"])}).first()[0]
        sess.execute(text("""
            UPDATE rv_item SET bundle_id = :b, bundle_status = :st, validation = :v
            WHERE id = :i
        """), {"b": bid, "st": status, "v": json.dumps(result), "i": item_id})

    return {"ok": True, "promoted": promo, "artifact": built["artifact"],
            "bundle_status": status, "validation": result,
            "bundle_id": str(bid)}


@router.post("/items/{item_id}/reject")
def reject(item_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    who = (body or {}).get("reviewer") or "reviewer"
    with session_scope() as sess:
        sess.execute(text("UPDATE rv_item SET state = 'rejected', completed_at = now() WHERE id = :i"),
                     {"i": item_id})
        sess.execute(text("INSERT INTO rv_action (rv_item_id, actor, action, detail) "
                          "VALUES (:i, :a, 'reject', :d)"),
                     {"i": item_id, "a": who, "d": json.dumps({"note": (body or {}).get("note")})})
    return {"ok": True, "state": "rejected"}


@router.get("/items/{item_id}/bundle")
def item_bundle(item_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        row = sess.execute(text("""
            SELECT bundle_json, status, validator, validation_ok, validation_issues, artifact
            FROM cn_fhir_bundle WHERE rv_item_id = :i ORDER BY created_at DESC LIMIT 1
        """), {"i": item_id}).mappings().first()
    if not row:
        raise HTTPException(404, "no bundle yet - approve the item first")
    return {"artifact": row["artifact"], "status": row["status"],
            "validator": row["validator"], "validation_ok": row["validation_ok"],
            "issues": row["validation_issues"], "bundle": row["bundle_json"]}


@router.get("/stats")
def stats() -> dict[str, Any]:
    with session_scope() as sess:
        r = sess.execute(text("""
            SELECT state, count(*) FROM rv_item GROUP BY state
        """)).all()
    return {"by_state": {k: v for k, v in r}}
