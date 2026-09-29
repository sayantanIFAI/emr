"""Clinical-context plausibility (E6 "medicine localisation", tier 3 of the hierarchy).

Priors RANK, they never decide: a drug whose indications do not fit the document's own
complaints/diagnoses (e.g. a nitrate for chest pain on a lumbar-spine prescription) is
re-ranked below plausible alternatives and FLAGGED for review. It is never removed and
never silently swapped - the reviewer sees both the reading and the reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# groups that fit almost any context - they never make a drug implausible
_GENERIC = {"pain_fever", "gi_acid"}


@dataclass
class Plausibility:
    status: str                    # fits | mismatch | unknown
    context_groups: list[str]
    drug_groups: list[str]
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "context_groups": self.context_groups,
                "drug_groups": self.drug_groups, "reason": self.reason}


def context_groups(texts: list[str], groups: dict[str, list[str]]) -> list[str]:
    """Indication groups named by the document's complaints / diagnoses / findings."""
    blob = " " + re.sub(r"[^a-z0-9 ]", " ", " ".join(texts).casefold()) + " "
    hit = []
    for code, kws in groups.items():
        for kw in kws:
            k = " " + re.sub(r"[^a-z0-9 ]", " ", kw.casefold()).strip() + " "
            if k.strip() and k in blob:
                hit.append(code)
                break
    return sorted(set(hit))


def assess(drug_indications: list[str] | None, ctx: list[str]) -> Plausibility:
    di = sorted(set(drug_indications or []))
    if not di or not ctx:
        return Plausibility("unknown", ctx, di, "no indication data" if not di else "no context")
    if set(di) & set(ctx) or set(di) & _GENERIC:
        return Plausibility("fits", ctx, di)
    return Plausibility("mismatch", ctx, di,
                        f"drug indicated for {', '.join(di)}; document context is "
                        f"{', '.join(ctx)}")


def rerank(cands: list[dict[str, Any]], ctx: list[str]) -> list[dict[str, Any]]:
    """Stable re-rank: plausible candidates first, score order within each band.
    Each candidate gets ``plausibility``; nothing is dropped."""
    for c in cands:
        p = assess((c.get("attrs") or {}).get("indications"), ctx)
        c["plausibility"] = p.as_dict()
    band = {"fits": 0, "unknown": 1, "mismatch": 2}
    return sorted(cands, key=lambda c: (band[c["plausibility"]["status"]], -c["score"]))
