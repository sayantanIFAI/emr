"""Evidence hierarchy evaluation for one fact (ARCHITECTURE §15.5).

  tier 0  pixels        grounding vetoes a value its pixels do not support
  tier 1  readings      independent engines agree / disagree / only one read it
  tier 2  lexical       alias cascade resolved it (level, class, collision)
  tier 3  priors        context plausibility - ranks and flags, never decides
  constraints           grammar + marketed-strength eliminate impossible values

Returns validate-style findings plus an ordered ``decision_trace`` stored on the fact
(and on the verified-fact ledger) so every gate decision is explainable after the fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from .disagreement import ocr_vs_terminology, strength_fits_product
from .grammar import check_medication_fields
from .grounding import ground_value

Finding = tuple[str, str, str]


@dataclass
class Assessment:
    evidence_state: str
    findings: list[Finding] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    winning_candidate_ids: list[str] = field(default_factory=list)


def _block_states(blocks: list[dict[str, Any]]) -> list[str]:
    return [((b.get("recognition") or {}).get("state") or "legacy") for b in blocks]


def _value_for_grounding(f: dict[str, Any], md: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(label, value) pairs whose pixels must support them. Numbers are the veto-grade
    part; names are checked for similarity. Free-text phrases (diagnoses the extractor
    may legitimately reword) are not grounded."""
    out: list[tuple[str, str]] = []
    ft = f["fact_type"]
    if ft == "medication" and md:
        if md.get("drug_text"):
            out.append(("drug_text", md["drug_text"]))
        if md.get("strength_num") is not None:
            s = float(md["strength_num"])
            out.append(("strength", str(int(s)) if s.is_integer() else str(s)))
        if md.get("frequency_code") and any(ch.isdigit() for ch in md["frequency_code"]):
            out.append(("frequency", md["frequency_code"].split()[0]))
    elif ft in ("lab_result", "vital_sign") and f.get("value_num") is not None:
        v = float(f["value_num"])
        out.append(("value", str(int(v)) if v.is_integer() else str(v)))
    return out


def assess_fact(f: dict[str, Any], md: dict[str, Any] | None, blocks: list[dict[str, Any]],
                cands: list[dict[str, Any]], *, reread: Any | None = None) -> Assessment:
    from ..validate.policy import worst_state

    states = _block_states(blocks)
    state = worst_state(states) if blocks else "legacy"
    a = Assessment(state)
    a.observation_ids = [str(o) for b in blocks for o in (b.get("observation_ids") or [])]
    ft = f["fact_type"]

    # ---- tier 1: independent readings ----
    rec = [(b.get("recognition") or {}) for b in blocks]
    a.trace.append({"tier": 1, "check": "engine_agreement", "state": state,
                    "lines": [{"text": b.get("text"), "state": r.get("state"),
                               "engines": r.get("engines")} for b, r in zip(blocks, rec)]})
    if state == "disagree":
        dis = [r.get("engines") for r in rec if r.get("state") == "disagree"]
        prefs = [(r.get("adjudication") or {}) for r in rec if r.get("state") == "disagree"]
        hint = "; ".join(f"adjudicator (advisory) prefers {p['preferred_text']!r}"
                         for p in prefs if p.get("verdict") == "prefers")
        a.findings.append(("blocker", "engine-disagreement",
                           f"handwriting engines disagree: {dis}" + (f" - {hint}" if hint else "")))
        a.trace.append({"tier": 1, "check": "qwen_adjudication", "advisory": True,
                        "results": prefs})
    elif state == "no_reading":
        a.findings.append(("blocker", "no-reading", "no engine produced a reading for this line"))
    elif state == "single_engine":
        a.findings.append(("warn", "single-engine",
                           "only one handwriting engine read this line (no corroboration)"))
    elif state == "page_level":
        a.findings.append(("warn", "page-level-evidence",
                           "line crops unavailable - page-level transcription only"))

    # ---- tier 0: pixel grounding ----
    if settings.grounding_enabled and blocks:
        readings = [b.get("text") or "" for b in blocks]
        for r in rec:
            readings += [t for t in (r.get("engines") or {}).values() if t]
            if r.get("rapidocr"):
                readings.append(r["rapidocr"])
        for label, val in _value_for_grounding(f, md):
            g = ground_value(val, readings, reread=reread)
            a.trace.append({"tier": 0, "check": f"grounding:{label}", "value": val, **g.as_dict()})
            if not g.numbers_ok and any(ch.isdigit() for ch in val):
                a.findings.append(("blocker", "grounding-failed",
                                   f"{label} '{val}' is not supported by the pixels "
                                   f"(best read: {g.matched_text!r})"))
            elif not g.grounded:
                code = "med-grounding-weak" if ft == "medication" else "grounding-weak"
                a.findings.append(("warn", code, f"{label} '{val}' only weakly matches the "
                                   f"pixels (similarity {g.similarity})"))

    # ---- constraints: grammar ----
    if ft == "medication" and md:
        for fld, why in check_medication_fields(md):
            a.findings.append(("blocker", "med-grammar-invalid", f"{fld}: {why}"))
            a.trace.append({"tier": "constraint", "check": f"grammar:{fld}", "result": why})

    # ---- tier 2 / 3: interpretation candidates ----
    if cands:
        sel = next((c for c in cands if c.get("is_selected")), None)
        coll = any(c.get("collision") for c in cands)
        ev = (sel or cands[0]).get("evidence") or {}
        a.trace.append({"tier": 2, "check": "alias_cascade",
                        "selected": (sel or {}).get("concept_id"),
                        "level": (sel or {}).get("resolved_by_level"),
                        "alias_class": (sel or {}).get("alias_class"), "collision": coll,
                        "top": [(c.get("concept_id"), float(c.get("score") or 0)) for c in cands[:3]]})
        if coll:
            a.findings.append(("blocker", "candidate-collision",
                               "reading matches several concepts equally: "
                               + ", ".join(str(c.get("normalized_text")) for c in cands if c.get("collision"))))
        elif ev.get("unresolved"):
            a.findings.append(("warn", "unresolved-reading",
                               f"'{ev.get('reading')}' did not resolve to a known concept"))
        elif sel and (sel.get("resolved_by_level") or 9) >= 3:
            a.findings.append(("warn", "fuzzy-resolution" if ft != "medication" else "med-fuzzy-resolution",
                               f"resolved only by fuzzy match to {sel.get('normalized_text')}"))
        if sel:
            a.winning_candidate_ids = [str(sel["id"])] if sel.get("id") else []
            pl = ev.get("plausibility") or {}
            a.trace.append({"tier": 3, "check": "context_plausibility", **pl})
            if pl.get("status") == "mismatch":
                a.findings.append(("blocker" if ft == "medication" else "warn",
                                   "med-indication-mismatch" if ft == "medication" else "indication-mismatch",
                                   pl.get("reason") or "drug does not fit the documented context"))
            attrs = ev.get("attrs") or {}
            if ft == "medication" and md:
                fits = strength_fits_product(md.get("strength_num"), attrs)
                if fits is False:
                    a.findings.append(("blocker", "med-strength-not-marketed",
                                       f"{sel.get('normalized_text')} is not made in strength "
                                       f"{md.get('strength_num')} (available {attrs.get('strengths_available')})"))
                a.trace.append({"tier": "constraint", "check": "marketed_strength", "fits": fits})
                if blocks and sel.get("normalized_text"):
                    ok, sim = ocr_vs_terminology(" ".join(b.get("text") or "" for b in blocks),
                                                 sel["normalized_text"])
                    a.trace.append({"tier": 2, "check": "ocr_vs_terminology", "ok": ok, "sim": sim})
    return a
