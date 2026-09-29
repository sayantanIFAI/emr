"""Pixel grounding (E4-S4): tier 0 of the evidence hierarchy - pixels veto.

A governed value is *grounded* when the pixels it claims to come from support it:
every number in the value appears (exactly, after look-alike normalisation) in some
reading of its evidence region, and its text is similar to a window of that reading.

Two passes, cheapest first:
  1. the readings already recorded for those lines (every engine, every observation);
  2. a fresh re-read of the evidence region cut from the SOURCE render with a margin
     (catches values that came from a neighbouring line or a clipped crop).
Grounding never proposes a value; it can only veto one.
"""
from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from .grammar import normalize_numeric_context, numbers_in


@dataclass
class GroundingResult:
    grounded: bool
    similarity: float
    numbers_ok: bool
    method: str                     # recorded | reread | none
    matched_text: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"grounded": self.grounded, "similarity": self.similarity,
                "numbers_ok": self.numbers_ok, "method": self.method,
                "matched_text": self.matched_text, **self.detail}


def partial_similarity(needle: str, hay: str) -> float:
    """Best similarity of ``needle`` against any same-length token window of ``hay``
    (the value is usually a part of the line: 'Telma 40' inside 'Tab Telma 40 1-0-1')."""
    n = normalize_numeric_context(needle).split()
    h = normalize_numeric_context(hay).split()
    if not n:
        return 1.0
    if not h:
        return 0.0
    k = len(n)
    target = " ".join(n)
    best = 0.0
    for w in range(max(1, k - 1), k + 2):
        for i in range(0, max(1, len(h) - w + 1)):
            win = " ".join(h[i:i + w])
            best = max(best, difflib.SequenceMatcher(None, target, win).ratio())
    # glued tokens ('Telma40') - compare without spaces as well
    best = max(best, difflib.SequenceMatcher(
        None, target.replace(" ", ""), "".join(h)).ratio() if len(h) <= k + 2 else 0.0)
    return round(best, 3)


def numbers_supported(value: str, reading: str) -> bool:
    """Every number of the value is present in the reading (multiset subset)."""
    need = Counter(numbers_in(value))
    have = Counter(numbers_in(reading))
    return all(have[n] >= c for n, c in need.items())


def ground_text(value: str, readings: list[str], *,
                min_similarity: float | None = None) -> GroundingResult:
    thr = settings.grounding_min_similarity if min_similarity is None else min_similarity
    best: tuple[float, bool, str] | None = None
    for r in readings:
        if not (r or "").strip():
            continue
        sim = partial_similarity(value, r)
        nums = numbers_supported(value, r)
        key = (nums, sim)
        if best is None or key > (best[1], best[0]):
            best = (sim, nums, r)
    if best is None:
        return GroundingResult(False, 0.0, False, "none", None, {"reason": "no readings"})
    sim, nums, txt = best
    return GroundingResult(nums and sim >= thr, sim, nums, "recorded", txt)


def ground_value(value: str, recorded: list[str], reread: Any | None = None) -> GroundingResult:
    """``reread`` is a zero-arg callable returning extra readings from a margin re-crop;
    it is only invoked when the recorded readings do not already ground the value."""
    if not (value or "").strip():
        return GroundingResult(True, 1.0, True, "none", None, {"reason": "empty value"})
    res = ground_text(value, recorded)
    if res.grounded or reread is None:
        return res
    try:
        extra = [t for t in (reread() or []) if t]
    except Exception as exc:  # noqa: BLE001 - a failed re-read cannot ground anything
        res.detail["reread_error"] = str(exc)[:200]
        return res
    res2 = ground_text(value, extra)
    if res2.grounded:
        res2.method = "reread"
        return res2
    res.detail["reread"] = res2.as_dict()
    return res
