"""Many written names -> ONE standard lab test (the mapping table).

A doctor writes the same test many ways ("creatinine", "creatine", "sr creatinine", "S. Creat", "RFT"). This table says
which standard test each written name stands for, and the screen shows the whole table so a person can see, add to and
switch off any row. It is consulted FIRST, before the Indian national list and the curated abbreviation table.

* The rows live in ``lab_test_alias`` (migration 0010). ``SEED`` below is what the application puts there on first use
  (``ensure_seed``); a row a person switched off stays off (rows are never deleted, only ``enabled`` = false).
* With no database (a unit test, a first start) the seed alone answers, so a lookup never fails.
* A name is matched whole, after lower-casing and dropping punctuation, and then again without a leading specimen word
  ("sr", "s", "serum", "plasma", "blood": "S. Creatinine" = "creatinine"). "urine creatinine" is NOT cut: it is a
  different test.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from ..logging import get_logger
from .indian_codes import norm

log = get_logger(__name__)

# (written name, the standard test, its LOINC code or None, note). Codes only where they are certain.
_CREAT_NOTE = "the owner asked for these to map to creatinine"
SEED: list[tuple[str, str, str | None, str]] = [
    ("creatinine", "Creatinine", "2160-0", ""),
    ("creatine", "Creatinine", "2160-0", "a common misspelling of creatinine; " + _CREAT_NOTE),
    ("sr creatinine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("sr creatine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("serum creatinine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("serum creatine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("s creatinine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("s creatine", "Creatinine", "2160-0", _CREAT_NOTE),
    ("creat", "Creatinine", "2160-0", _CREAT_NOTE),
    ("s creat", "Creatinine", "2160-0", _CREAT_NOTE),
    ("sr creat", "Creatinine", "2160-0", _CREAT_NOTE),
    ("rft", "Creatinine", "2160-0",
     "RFT is a PANEL (urea, creatinine, electrolytes); mapped to creatinine because the owner asked. Switch off here to keep it a panel."),
    ("renal function test", "Creatinine", "2160-0", "a panel; mapped to creatinine because the owner asked"),
    ("renal function tests", "Creatinine", "2160-0", "a panel; mapped to creatinine because the owner asked"),
    ("kft", "Creatinine", "2160-0", "a panel; mapped to creatinine because the owner asked"),
    ("kidney function test", "Creatinine", "2160-0", "a panel; mapped to creatinine because the owner asked"),
    ("hba1c", "HbA1c", "4548-4", ""),
    ("glycosylated hemoglobin", "HbA1c", "4548-4", ""),
    ("glycated hemoglobin", "HbA1c", "4548-4", ""),
    ("glycosylated haemoglobin", "HbA1c", "4548-4", ""),
    ("glycated haemoglobin", "HbA1c", "4548-4", ""),
    ("a1c", "HbA1c", "4548-4", ""),
    ("tsh", "TSH", "3016-3", ""),
    ("thyroid stimulating hormone", "TSH", "3016-3", ""),
    ("fbs", "Fasting blood sugar", "1558-6", ""),
    ("fbg", "Fasting blood sugar", "1558-6", ""),
    ("fasting blood sugar", "Fasting blood sugar", "1558-6", ""),
    ("fasting blood glucose", "Fasting blood sugar", "1558-6", ""),
    ("fasting glucose", "Fasting blood sugar", "1558-6", ""),
    ("ppbs", "Post-prandial blood sugar", None, ""),
    ("post prandial blood sugar", "Post-prandial blood sugar", None, ""),
    ("postprandial blood sugar", "Post-prandial blood sugar", None, ""),
    ("pp2bs", "Post-prandial blood sugar", None, ""),
]

_LEADING = ("sr ", "s ", "serum ", "plasma ", "blood ")


@dataclass(frozen=True)
class Mapped:
    alias: str
    canonical: str
    loinc: str | None
    note: str
    source: str                     # "seed" | "user"


_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "rows": None}
_TTL_S = 30.0


def _seed_rows() -> dict[str, Mapped]:
    return {norm(a): Mapped(a, c, l, n, "seed") for a, c, l, n in SEED}


def invalidate() -> None:
    with _lock:
        _cache["rows"] = None
        _cache["at"] = 0.0


def _load() -> dict[str, Mapped]:
    """The enabled rows (database, with the seed underneath), cached for a short time."""
    now = time.time()
    with _lock:
        if _cache["rows"] is not None and now - _cache["at"] < _TTL_S:
            return _cache["rows"]
    rows = _seed_rows()
    try:
        from ..config import settings

        if not settings.lab_mapping_db:
            raise RuntimeError("database not used")
        from ..db import session_scope

        with session_scope() as sess:
            got = sess.execute(text(
                "SELECT alias_key, alias, canonical, loinc, coalesce(note,'') AS note, source, enabled FROM lab_test_alias"
            )).mappings().all()
        for r in got:
            if r["enabled"]:
                rows[r["alias_key"]] = Mapped(r["alias"], r["canonical"], r["loinc"], r["note"], r["source"])
            else:
                rows.pop(r["alias_key"], None)               # switched off: the seed does not bring it back
    except Exception as exc:  # noqa: BLE001 - no database / no table yet: the seed alone answers
        log.info("lab_mapping_seed_only", error=str(exc)[:120])
    with _lock:
        _cache["rows"], _cache["at"] = rows, now
    return rows


def lookup(name: str | None) -> Mapped | None:
    """The standard test this written name stands for, or None."""
    k = norm(name)
    if not k:
        return None
    rows = _load()
    hit = rows.get(k)
    if hit is not None:
        return hit
    for lead in _LEADING:
        if k.startswith(lead):
            hit = rows.get(k[len(lead):])
            if hit is not None:
                return hit
    return None


def ensure_seed() -> int:
    """Put the seed rows into ``lab_test_alias`` (only the ones that are not there); returns how many were added."""
    from ..db import session_scope

    added = 0
    with session_scope() as sess:
        for a, c, l, n in SEED:
            r = sess.execute(text(
                "INSERT INTO lab_test_alias (alias_key, alias, canonical, loinc, note, source) "
                "VALUES (:k, :a, :c, :l, :n, 'seed') ON CONFLICT (alias_key) DO NOTHING RETURNING 1"),
                {"k": norm(a), "a": a, "c": c, "l": l, "n": n}).first()
            added += 1 if r else 0
    invalidate()
    return added


def table(sess: Any) -> list[dict[str, Any]]:
    """Every row, for the screen (switched-off rows included, so they can be switched back on)."""
    rows = sess.execute(text(
        "SELECT alias_key, alias, canonical, loinc, coalesce(note,'') AS note, source, enabled, updated_at "
        "FROM lab_test_alias ORDER BY canonical, alias")).mappings().all()
    return [{**dict(r), "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None} for r in rows]


def upsert(sess: Any, alias: str, canonical: str, loinc: str | None, note: str | None) -> dict[str, Any]:
    key = norm(alias)
    canonical = " ".join((canonical or "").split())
    if not key or not canonical:
        raise ValueError("Both the written name and the standard test are needed.")
    if len(key) > 80 or len(canonical) > 120:
        raise ValueError("That name is too long.")
    sess.execute(text(
        "INSERT INTO lab_test_alias (alias_key, alias, canonical, loinc, note, source, enabled) "
        "VALUES (:k, :a, :c, :l, :n, 'user', true) "
        "ON CONFLICT (alias_key) DO UPDATE SET alias=:a, canonical=:c, loinc=:l, note=:n, enabled=true, updated_at=now()"),
        {"k": key, "a": " ".join(alias.split()), "c": canonical, "l": (loinc or "").strip() or None, "n": (note or "").strip() or None})
    invalidate()
    return {"alias_key": key, "alias": alias.strip(), "canonical": canonical}


def set_enabled(sess: Any, alias_key: str, enabled: bool) -> bool:
    r = sess.execute(text("UPDATE lab_test_alias SET enabled=:e, updated_at=now() WHERE alias_key=:k RETURNING 1"),
                     {"e": enabled, "k": alias_key}).first()
    invalidate()
    return bool(r)
