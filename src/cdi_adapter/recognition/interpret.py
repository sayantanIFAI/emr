"""Interpretation (tier 2 + 3): recorded candidates for every drug / investigation fact.

terminology.service binds the standard code as before; this step adds the explainable
layer the governance gate needs - which concept each reading resolved to, at which
cascade level, whether it collided with another concept, and whether it is plausible in
this document's clinical context. Candidates are evidence, not decisions: the gate
(validate) reads them, a reviewer can pick another one.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .. import repo
from ..logging import get_logger
from . import plausibility as P
from .alias import KB, candidates, mark_collisions

log = get_logger(__name__)

_DOMAIN = {"medication": "drug", "investigation_order": "lab_order"}
_CONTEXT_TYPES = ("condition", "symptom", "finding")


def _reading(f: dict[str, Any], md: dict[str, Any] | None) -> str:
    if f["fact_type"] == "medication" and md:
        base = md.get("drug_text") or f.get("local_text") or ""
        if md.get("strength_num") is not None and not any(ch.isdigit() for ch in base):
            s = md["strength_num"]
            base = f"{base} {int(s) if float(s).is_integer() else s}"
        return base
    return f.get("value_code_display") or f.get("local_text") or ""


def interpret_document(sess: Any, document_id: str, facts: list[dict[str, Any]]) -> dict[str, int]:
    try:
        with sess.begin_nested():
            kb = KB.load(sess)
    except Exception as exc:  # noqa: BLE001 - KB tables missing (pre-0005 DB)
        log.warning("interpret_skipped", document_id=document_id, error=str(exc)[:200])
        return {}
    ctx_texts = [f.get("local_text") or "" for f in facts if f["fact_type"] in _CONTEXT_TYPES]
    ctx = P.context_groups(ctx_texts, kb.groups)
    doc = repo.get_document(sess, document_id) or {}
    prac = doc.get("practitioner_id")
    counts = {"facts": 0, "resolved": 0, "collisions": 0, "implausible": 0}
    for f in facts:
        dom = _DOMAIN.get(f["fact_type"])
        if not dom:
            continue
        md = repo.get_medication_detail(sess, f["id"]) if f["fact_type"] == "medication" else None
        reading = _reading(f, md)
        cands = candidates(kb, reading, dom, practitioner_id=str(prac) if prac else None)
        collision = mark_collisions(cands)
        if dom == "drug":
            cands = P.rerank(cands, ctx)
        prov = repo.get_fact_provenance(sess, f["id"])
        blocks = repo.get_blocks_by_ids(sess, [b for p in prov for b in (p.get("ocr_block_ids") or [])])
        obs_ids = [o for b in blocks for o in (b.get("observation_ids") or [])]
        repo.delete_candidates_for_fact(sess, f["id"])
        rows = []
        for i, c in enumerate(cands):
            rows.append({
                "fact_id": f["id"], "observation_ids": obs_ids, "domain": dom,
                "concept_id": c["concept_id"], "normalized_text": c["normalized_text"],
                "code_system": c.get("code_system"), "code": c.get("code"),
                "code_display": c.get("code_display"), "source": c["source"],
                "alias_class": c["alias_class"], "resolved_by_level": c["resolved_by_level"],
                "score": c["score"], "is_selected": i == 0 and not collision,
                "collision": bool(c.get("collision")),
                "evidence": {"reading": reading, "alias": c["alias"],
                             "plausibility": c.get("plausibility"), "context_groups": ctx,
                             "attrs": c.get("attrs")},
            })
        if not cands:   # unresolved: record the literal reading so the gap is visible
            rows.append({"fact_id": f["id"], "observation_ids": obs_ids, "domain": dom,
                         "normalized_text": reading, "source": "literal", "score": 0.0,
                         "evidence": {"reading": reading, "unresolved": True,
                                      "context_groups": ctx}})
        repo.insert_candidates(sess, rows)
        counts["facts"] += 1
        counts["resolved"] += bool(cands)
        counts["collisions"] += collision
        if cands and (cands[0].get("plausibility") or {}).get("status") == "mismatch":
            counts["implausible"] += 1
        # a KB concept with a standard code fills a gap the seed map left (never overrides)
        top = cands[0] if cands and not collision else None
        if top and top.get("code") and f.get("code_status") != "bound":
            sess.execute(text(
                "UPDATE clinical_fact SET code_system = :s, code = :c, code_display = :d, "
                "code_status = 'bound', confidence_terminology = :t WHERE id = :id"),
                {"s": top["code_system"], "c": top["code"], "d": top["code_display"],
                 "t": 0.9 if top["resolved_by_level"] <= 2 else 0.75, "id": str(f["id"])})
    return counts


def selected_candidate(sess: Any, fact_id: Any) -> dict[str, Any] | None:
    r = sess.execute(text(
        "SELECT * FROM interpretation_candidate WHERE fact_id = :f "
        "ORDER BY is_selected DESC, score DESC LIMIT 1"), {"f": str(fact_id)}).mappings().first()
    return dict(r) if r else None


def fact_candidates(sess: Any, fact_id: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in sess.execute(text(
        "SELECT * FROM interpretation_candidate WHERE fact_id = :f ORDER BY score DESC"),
        {"f": str(fact_id)}).mappings()]
