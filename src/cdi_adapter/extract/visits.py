"""Dated visits on a prescription (front page, back page, older entries below a ruled line): which one is the latest.

A patient's papers often hold more than one visit: a continuation sheet, the back of the page, or older entries kept below
a ruled line under the newest one. The doctor's booking and the lab tests that matter are the LATEST visit's. Each page is
read on its own (``earlier_entries`` = the other dated entries the reader saw on that page), then this module

1. lists every dated entry (page, the date as written, its lab tests, its follow-up),
2. picks the latest by date (dd/mm/yy, dd-Mon-yyyy, yyyy-mm-dd), and
3. puts that entry's tests and follow-up where the rest of the pipeline expects them, so they go through the same lab
   gate and checks; every other entry stays in ``visits`` for the screen, marked as earlier.

Nothing is guessed: an entry with no readable date is never ranked above one with a date; with no dates anywhere the first
page's main entry is the current one (the front of the paper). Pure functions, no model, no database.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

_IDENTITY = ("patient", "prescriber")


def _text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or item.get("name") or "").strip()
    return str(item or "").strip()


def _texts(items: Any) -> list[str]:
    return [t for t in (_text(i) for i in (items or [])) if t]


def _date(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    from .service import _parse_date          # lazy: service imports this module

    d, _prec = _parse_date(s)
    return d


def _follow_text(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("text") or v.get("value")
    return v.strip() if isinstance(v, str) and v.strip() else None


def entries_of(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every dated entry on the pages, in page order (a page's main entry first, then its other entries)."""
    out: list[dict[str, Any]] = []
    for pno, p in enumerate(payloads, start=1):
        if not isinstance(p, dict):
            continue
        out.append({"page": pno, "kind": "main", "date_text": p.get("encounter_date") if isinstance(p.get("encounter_date"), str) else None,
                    "investigations": p.get("investigations") or [], "follow_up": p.get("follow_up"), "src": p})
        for e in p.get("earlier_entries") or []:
            if not isinstance(e, dict):
                continue
            inv, fu = e.get("investigations") or [], _follow_text(e.get("follow_up"))
            dt = e.get("date_text") if isinstance(e.get("date_text"), str) else None
            if not (inv or fu or dt):
                continue
            out.append({"page": pno, "kind": "other", "date_text": dt, "investigations": inv, "follow_up": fu, "src": p})
    return out


def pick_latest(entries: list[dict[str, Any]]) -> int:
    """Index of the latest entry: the newest known date; ties and no dates at all -> the earliest in page order."""
    best, best_d = 0, None
    for i, e in enumerate(entries):
        d = _date(e["date_text"])
        if d is not None and (best_d is None or d > best_d):
            best, best_d = i, d
    return best


def merge(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """One payload from the pages' payloads (page order). The latest entry's tests / follow-up / date become the payload's;
    ``visits`` lists every entry newest first; ``_latest_page`` (1-based) is the page the latest entry is on."""
    pages = [p for p in payloads if isinstance(p, dict)]
    if not pages:
        return {}
    entries = entries_of(pages)
    li = pick_latest(entries)
    latest = entries[li]
    merged = copy.deepcopy(pages[0])
    for key in _IDENTITY:                                   # who it is: the first page that says it, field by field
        acc: dict[str, Any] = {}
        for p in pages:
            sub = p.get(key)
            if not isinstance(sub, dict):
                continue
            for k, v in sub.items():
                if k == "clinic" and isinstance(v, dict):
                    c = acc.setdefault("clinic", {})
                    for ck, cv in v.items():
                        if c.get(ck) in (None, "", []) and cv not in (None, "", []):
                            c[ck] = cv
                elif acc.get(k) in (None, "", []) and v not in (None, "", []):
                    acc[k] = v
        if acc:
            merged[key] = acc
    src = latest["src"]
    for k in ("diagnoses", "advice"):
        if k in src:
            merged[k] = copy.deepcopy(src[k])
    if "investigation_preparation" in src:
        merged["investigation_preparation"] = copy.deepcopy(src["investigation_preparation"])
    merged["investigations"] = copy.deepcopy(latest["investigations"])
    fu = latest["follow_up"]
    merged["follow_up"] = _follow_text(fu) if not isinstance(fu, str) else (fu.strip() or None)
    merged["encounter_date"] = latest["date_text"]
    merged.pop("earlier_entries", None)
    if any(p.get("_partial") for p in pages):
        merged["_partial"] = True

    def _rank(i: int) -> tuple[bool, float, int]:
        d = _date(entries[i]["date_text"])
        return (d is None, -d.timestamp() if d else 0.0, i)               # newest first; undated last; page order breaks ties

    ranked = sorted(range(len(entries)), key=_rank)
    visits: list[dict[str, Any]] = []
    for i in ranked:
        e = entries[i]
        d = _date(e["date_text"])
        visits.append({"page": e["page"], "where": f"page {e['page']}" + ("" if e["kind"] == "main" else ", another dated entry"),
                       "date": d.date().isoformat() if d else None, "date_text": e["date_text"], "is_latest": i == li,
                       "lab_tests": _texts(e["investigations"]), "follow_up": _follow_text(e["follow_up"])})
    if len(entries) > 1 or any(v["date"] for v in visits):
        merged["visits"] = visits
    merged["_latest_page"] = latest["page"]
    return merged
