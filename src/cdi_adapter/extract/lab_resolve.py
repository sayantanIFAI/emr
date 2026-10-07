"""Lab test name -> the test and its LOINC code, from the Indian national list first.

Order of authority: Common Lab Codes for India (CLCI) -> the curated abbreviation / panel table (lab_gazetteer) ->
nothing. CLCI is single analytes; the table adds what doctors abbreviate and the panels (CBC, KFT, LFT ...).

``status`` says how sure the code is: ``bound`` (one code, or the table agrees with one of several) or ``candidate``
(the list gives several, for example a spot and a 24 hour urine test, and the page does not say which).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from . import indian_codes, lab_gazetteer, lab_mapping
from .indian_codes import norm


@dataclass(frozen=True)
class Resolved:
    kind: str                       # "test" | "analyte" | "catalogue"
    loinc: str | None
    status: str | None              # "bound" | "candidate" | None (a recognised name that has no code)
    candidates: tuple[str, ...]
    long_name: str | None
    source: str                     # "mapping" | "CLCI" | "gazetteer"
    fuzzy: bool


def resolve(text: str | None) -> Resolved | None:
    mp = lab_mapping.lookup(text)              # the mapping table first: many written names -> one standard test
    if mp is not None:
        return Resolved("test", mp.loinc, "bound" if mp.loinc else None, (mp.loinc,) if mp.loinc else (), mp.canonical,
                        "mapping", False)
    cl = indian_codes.lab_candidates(text)
    cur = lab_gazetteer.lookup(text)
    if cl:
        codes = tuple(dict.fromkeys(c.loinc for c in cl))
        if len(codes) == 1:
            loinc, status = codes[0], "bound"
        elif cur is not None and cur.loinc in codes:
            loinc, status = cur.loinc, "bound"                 # the curated table picks one of the national codes
        else:
            loinc, status = codes[0], "candidate"
        return Resolved("test", loinc, status, codes, cl[0].lcn, "CLCI", False)
    if cur is not None:
        return Resolved(cur.kind, cur.loinc, "bound" if cur.loinc else None, (cur.loinc,) if cur.loinc else (),
                        cur.long_name, "gazetteer", cur.fuzzy)
    return None


def suggest(text: str | None, k: int = 5, floor: float = 0.6) -> list[str]:
    """Reference names an unrecognised entry might be, for the model to choose among (never applied on its own)."""
    n = norm(text)
    if len(n) < 3:
        return []
    floor = max(floor, 0.6 if len(n) <= 4 else 0.74)       # one misread letter is a big change in "PBS", a small one in a long word
    names: dict[str, str] = {}
    for nm in indian_codes.suggest_labs(text, k=k, floor=floor):
        names.setdefault(norm(nm), nm)
    g = lab_gazetteer.load()
    for alias, (_kind, row) in g.alias.items():
        if len(alias) < 2 or abs(len(alias) - len(n)) > max(3, len(n) // 2):
            continue
        if difflib.SequenceMatcher(None, n, alias).ratio() >= floor and row:
            names.setdefault(alias, (row.get("short_name") or row.get("long_name") or alias).strip())
    scored = sorted(((difflib.SequenceMatcher(None, n, key).ratio(), key) for key in names), reverse=True)
    return [names[key] for _r, key in scored[:k]]
