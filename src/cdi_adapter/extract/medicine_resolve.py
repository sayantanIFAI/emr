"""Medicine names: which reference name is this, and is it a medicine at all? (the medicine half of the hybrid)

The same rule as for lab tests (``resolve_llm``): a handwritten brand name is rarely read exactly, so the free
reading is checked against the reference lists (the medicine dataset and the Indian drug list, kept outside git), and
when it is close to one or more reference names the MODEL is asked to CHOOSE among them, never to write a name. What
it chooses is kept apart from what was written.

Also here: ``advice_like`` -- "steam inhalation", "gargle with warm water", "plenty of fluids" are advice, and a model
that lists them as medicines makes the medicine table wrong. A line is called advice only when it carries advice words
AND nothing says it is a medicine (no list hit, no strength, no dose pattern).
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from . import indian_codes
from .indian_codes import norm
from .medicine_lexicon import _PREFIX, _WORD, load, medicine_match

_ADVICE = re.compile(
    r"\b(steam|inhal\w*|gargl\w*|warm\s+water|plenty|fluids?|ors|rest\b|diet|avoid|exercise|walk\w*|sleep|"
    r"bed\s*rest|hydrat\w*|drink|salt|sugar|oil|massage|hot\s+(?:water|compress)|compress|physiotherapy)", re.I)
_DOSE = re.compile(r"\d\s*(?:mg|mcg|ug|g|ml|iu|%|units?)\b|\b\d\s*-\s*\d\s*-\s*\d\b|\b(?:od|bd|bid|tds|qid|hs|sos|stat)\b", re.I)


def known(text: str | None) -> bool:
    """On a reference list (a misread letter or two allowed): a medicine."""
    return bool(medicine_match(text) or indian_codes.drug_lookup(text))


def advice_like(text: str | None, *, has_dose: bool = False) -> bool:
    t = text or ""
    if has_dose or _DOSE.search(t) or known(t):
        return False
    return bool(_ADVICE.search(t))


def first_word(text: str | None) -> str:
    words = _WORD.findall(_PREFIX.sub("", text or "", count=1))
    return words[0].lower() if words else ""


def suggest(text: str | None, k: int = 5, floor: float = 0.62) -> list[str]:
    """Reference medicine names a misread entry might be, best first (for the model to choose among)."""
    w = first_word(text)
    if len(w) < 4:
        return []
    pool: set[str] = set()
    lex = load()
    if lex is not None:
        for n in (len(w) - 2, len(w) - 1, len(w), len(w) + 1, len(w) + 2):
            pool.update(lex.buckets.get((w[0], n), ()))
    idx = indian_codes.drugs()
    if idx is not None:
        for n in (len(w) - 2, len(w) - 1, len(w), len(w) + 1, len(w) + 2):
            pool.update(idx.buckets.get((w[0], n), ()))
    if not pool:
        return []
    sm = difflib.SequenceMatcher(None, "", w)
    scored: list[tuple[float, str]] = []
    for cand in pool:
        sm.set_seq1(cand)
        if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
            continue
        r = sm.ratio()
        if r >= floor:
            scored.append((r, cand))
    scored.sort(reverse=True)
    return [c.capitalize() for _r, c in scored[:k]]


def pending(names: list[str], max_items: int = 12) -> list[tuple[str, list[str]]]:
    """Medicines the lists do not place exactly but that are close to reference names (only those with candidates)."""
    out: list[tuple[str, list[str]]] = []
    for n in dict.fromkeys(names):
        if not n or advice_like(n):
            continue
        m = medicine_match(n)
        if m and m == first_word(n):
            continue                                       # an exact reference name: nothing to choose
        dm = indian_codes.drug_lookup(n)
        if dm is not None and not dm.fuzzy:
            continue
        cands = suggest(n)
        if cands:
            out.append((n, cands))
    return out[:max_items]


def reference_name(resolved: dict[str, str], written: str) -> str | None:
    return resolved.get(written)


def as_dict(x: Any) -> dict[str, str]:
    return x if isinstance(x, dict) else {}
