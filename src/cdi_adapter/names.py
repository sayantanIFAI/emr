"""Comparing person names as handwriting gives them: titles, spacing and small misreadings do not make a different person."""
from __future__ import annotations

import difflib
import re

_TITLES = re.compile(r"^(?:mr|mrs|ms|miss|master|baby|dr|smt|shri|sri|sh|late)\b\.?\s*", re.I)


def name_key(name: str | None) -> str:
    """A name for comparing: lower case, no title, letters and single spaces only."""
    n = _TITLES.sub("", (name or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", n.casefold())).strip()


def similarity(a: str | None, b: str | None) -> float:
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 1.0 if ka == kb else 0.0
    return 1.0 if ka == kb else difflib.SequenceMatcher(None, ka, kb).ratio()


def alike(a: str | None, b: str | None, threshold: float) -> bool:
    return similarity(a, b) >= threshold


def consensus(candidates: list[str], threshold: float = 0.85) -> tuple[str | None, int, int]:
    """``(the name most readings agree on, how many agree, how many readings)``. The shown name is the reading closest to the
    others in the winning group (its medoid); empty readings are ignored; ties go to the earlier reading."""
    cands = [c.strip() for c in candidates if isinstance(c, str) and name_key(c)]
    if not cands:
        return None, 0, 0
    groups: list[list[str]] = []
    for c in cands:
        for g in groups:
            if alike(g[0], c, threshold):
                g.append(c)
                break
        else:
            groups.append([c])
    best = max(groups, key=len)                                   # max keeps the earliest group on a tie
    medoid = max(best, key=lambda x: (sum(similarity(x, y) for y in best), -best.index(x)))
    return medoid, len(best), len(cands)
