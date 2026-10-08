"""Comparing person names as handwriting gives them: titles, spacing and small misreadings do not make a different person."""
from __future__ import annotations

import difflib
import re

_TITLES = re.compile(r"^(?:mr|mrs|ms|miss|master|baby|dr|smt|shri|sri|sh|late)\b\.?\s*", re.I)


def name_key(name: str | None) -> str:
    """A name for comparing: lower case, no title, letters and single spaces only."""
    n = _TITLES.sub("", (name or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", n.casefold())).strip()


_ORG_WORDS = re.compile(r"\b(?:limited|ltd|pvt|private|hospital|hospitals|clinic|clinics|centre|center|health|healthcare|lifestyle|"
                        r"diagnostic|diagnostics|pharmacy|medical|laboratory|laboratories|nursing|polyclinic|institute|foundation|"
                        r"trust|corporation|enterprises|company)\b", re.I)


def org_like(name: str | None) -> bool:
    """True for a company / hospital name ("Sanjeevani Health and Lifestyle Private Limited"): never a patient's name."""
    return bool(name and _ORG_WORDS.search(name))


def similarity(a: str | None, b: str | None) -> float:
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 1.0 if ka == kb else 0.0
    return 1.0 if ka == kb else difflib.SequenceMatcher(None, ka, kb).ratio()


def alike(a: str | None, b: str | None, threshold: float) -> bool:
    return similarity(a, b) >= threshold


def prefer_complete(chosen: str | None, readings: list[str], threshold: float = 0.85) -> str | None:
    """A model often cuts a long name short ("Smita Gupta" for "Smita Gupta Gangopadhyay"). When another reading begins with the
    same words as ``chosen`` and goes on, that fuller reading is the better suggestion. Never shortens, never invents: it only
    picks among the readings made. Titles are ignored; the first of equally full readings wins."""
    if not chosen:
        return chosen
    base = name_key(chosen).split()
    best, best_n = chosen, len(base)
    for r in readings:
        toks = name_key(r).split()
        if len(toks) > best_n and len(toks) <= best_n + 2 and all(alike(a, b, threshold) for a, b in zip(base, toks[:len(base)])):
            best, best_n = r, len(toks)
    return best


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
