"""Alias engine (E6): reading -> ranked concept candidates. Recognition != normalisation:
this runs on text that recognition already produced and never rewrites that text.

Resolution cascade (the level that resolved a candidate is recorded):
  L1 exact alias           class C (this doctor's verified habit) > A (verified) > B (generated)
  L2 normalised alias      numeric-context normalisation, dose-form words stripped
  L3 fuzzy alias           similarity over aliases; numbers must match exactly
  L4 context re-rank       indication plausibility (plausibility.py) - ranks, never decides
  L5-L8                    exemplars / embeddings / reranker / Qwen adjudication - not built:
                           anything unresolved at L3 goes to a human (E6 backlog)

Collision: two different concepts within ``COLLISION_MARGIN`` of the top score -> the
candidate set is ambiguous and the fact must be reviewed.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from .grammar import normalize_numeric_context, numbers_in

_FORM_WORDS = {"tab", "tab.", "tabs", "cap", "cap.", "caps", "tablet", "capsule", "syp",
               "syrup", "inj", "inj.", "injection", "susp", "cream", "oint", "gel", "drop",
               "drops", "t.", "c."}
_CLASS_RANK = {"C": 3, "A": 2, "B": 1}
FUZZY_MIN = 0.82
COLLISION_MARGIN = 0.03


def norm_alias(s: str) -> str:
    t = normalize_numeric_context(s)
    t = re.sub(r"(\d)\s*(mg|mcg|g|ml|iu)\b", r"\1 \2", t)
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)            # 'telma40' -> 'telma 40'
    t = re.sub(r"[^\w½%/. ]", " ", t)
    toks = [x for x in t.split() if x not in _FORM_WORDS]
    toks = [x for x in toks if x not in ("mg",)]       # 'telma 40 mg' == 'telma 40'
    return " ".join(toks).strip()


@dataclass
class KB:
    concepts: dict[str, dict[str, Any]]
    aliases: list[dict[str, Any]]
    groups: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, sess: Any) -> "KB":
        concepts = {r["id"]: dict(r) for r in sess.execute(
            text("SELECT * FROM kb_concept WHERE active")).mappings()}
        aliases = [dict(r) for r in sess.execute(text(
            "SELECT concept_id, alias, alias_norm, alias_class, practitioner_id FROM kb_alias"
        )).mappings() if r["concept_id"] in concepts]
        groups = {r["code"]: list(r["keywords"]) for r in sess.execute(
            text("SELECT code, keywords FROM kb_indication_group")).mappings()}
        return cls(concepts, aliases, groups)


def _cand(kb: KB, a: dict[str, Any], score: float, level: int, source: str,
          reading: str) -> dict[str, Any]:
    c = kb.concepts[a["concept_id"]]
    return {"concept_id": c["id"], "domain": c["domain"], "normalized_text": c["canonical_name"],
            "code_system": c.get("code_system"), "code": c.get("code"),
            "code_display": c.get("code_display") or c["canonical_name"],
            "attrs": c.get("attrs") or {}, "alias": a["alias"],
            "alias_class": a["alias_class"], "resolved_by_level": level, "source": source,
            "score": round(score, 4), "reading": reading}


def candidates(kb: KB, reading: str, domain: str, practitioner_id: str | None = None,
               limit: int = 5) -> list[dict[str, Any]]:
    """Ranked candidates for one reading (best first, one per concept)."""
    if not (reading or "").strip():
        return []
    raw = reading.strip().casefold()
    key = norm_alias(reading)
    pool = [a for a in kb.aliases if kb.concepts[a["concept_id"]]["domain"] == domain
            and (a["alias_class"] != "C" or (practitioner_id and
                                              str(a["practitioner_id"]) == str(practitioner_id)))]
    best: dict[str, dict[str, Any]] = {}

    def keep(c: dict[str, Any]) -> None:
        cur = best.get(c["concept_id"])
        rank = (c["score"], _CLASS_RANK[c["alias_class"]], -c["resolved_by_level"])
        if cur is None or rank > (cur["score"], _CLASS_RANK[cur["alias_class"]],
                                  -cur["resolved_by_level"]):
            best[c["concept_id"]] = c

    src = {"C": "doctor_alias", "A": "verified_alias", "B": "generated_alias"}
    for a in pool:
        if a["alias"].casefold() == raw:                                   # L1
            keep(_cand(kb, a, 1.0, 1, "literal" if a["alias_class"] == "A" else src[a["alias_class"]], reading))
        elif norm_alias(a["alias"]) == key or a["alias_norm"] == key:      # L2
            keep(_cand(kb, a, 0.97, 2, "normalized_alias", reading))
    if not best:                                                           # L3
        rnums = numbers_in(key)
        for a in pool:
            an = norm_alias(a["alias"])
            if numbers_in(an) and numbers_in(an) != rnums:
                continue            # numbers are never fuzzed: 'Telma 20' is not 'Telma 40'
            sim = difflib.SequenceMatcher(None, key, an).ratio()
            # a reading that carries the alias plus extra words ('telma 40 1-0-1')
            if an and key.startswith(an + " "):
                sim = max(sim, 0.9)
            if sim >= FUZZY_MIN:
                keep(_cand(kb, a, sim * 0.95, 3, "fuzzy", reading))
    out = sorted(best.values(), key=lambda c: (-c["score"], -_CLASS_RANK[c["alias_class"]]))
    return out[:limit]


def mark_collisions(cands: list[dict[str, Any]]) -> bool:
    if len(cands) < 2:
        return False
    top = cands[0]["score"]
    coll = [c for c in cands if top - c["score"] <= COLLISION_MARGIN]
    if len(coll) > 1:
        for c in coll:
            c["collision"] = True
        return True
    return False
