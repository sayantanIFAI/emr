"""Finding patients and their prescriptions on the upload screen (no dropdown of thousands: type a part of the mobile number).

A patient here is a (mobile number, name) pair, as the front desk sees it: the mobile number is typed with the upload, the
name is read from the page. Two people who share a phone are two patients. Everything is plain SQL on ``source_document``
(migration 0010); results are capped, so a search never returns thousands of rows.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from sqlalchemy import text

from ..db import session_scope

SEARCH_LIMIT = 10
LIST_LIMIT = 100
_TITLES = re.compile(r"^(?:mr|mrs|ms|miss|master|baby|dr|smt|shri|sri|sh|late)\.?\s*", re.I)
SAME_NAME = 0.78            # handwriting is read a little differently each time ("Onkar" / "Oukar"): near names are one patient


def name_key(name: str | None) -> str:
    """A name for comparing: lower case, no title, letters and single spaces only."""
    n = _TITLES.sub("", (name or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", n.casefold())).strip()


def same_patient(a: str | None, b: str | None) -> bool:
    """Two readings of a name on the SAME mobile number are one patient when they are alike (an empty name matches only an empty one)."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return ka == kb
    return ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= SAME_NAME


def _cluster(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge rows of one mobile number whose names are alike; the shown name is the one read most often."""
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: -x["prescriptions"]):
        for g in out:
            if g["phone"] == r["phone"] and same_patient(g["name"], r["name"]):
                g["prescriptions"] += r["prescriptions"]
                g["last_uploaded"] = max(g["last_uploaded"] or "", r["last_uploaded"] or "") or None
                g["names"].append(r["name"])
                break
        else:
            out.append({**r, "names": [r["name"]]})
    out.sort(key=lambda g: g["last_uploaded"] or "", reverse=True)
    return out


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def search(q: str) -> list[dict[str, Any]]:
    """Patients whose mobile number STARTS WITH the digits typed, or (when letters are typed) whose name contains the text.
    One row per (mobile, name): ``{phone, name, prescriptions, last_uploaded}``, newest first."""
    q = (q or "").strip()
    digits = re.sub(r"\D", "", q)
    if digits and not re.search(r"[A-Za-z]", q):
        if len(digits) < 2:
            return []
        where, arg = "phone LIKE :a", {"a": digits[:10] + "%"}
    elif len(q) >= 2:
        where, arg = "patient_name ILIKE :a", {"a": "%" + re.sub(r"[%_\\]", "", q)[:60] + "%"}
    else:
        return []
    with session_scope() as sess:
        rows = sess.execute(text(
            f"SELECT phone, coalesce(patient_name, '') AS name, count(*) AS n, max(ingested_at) AS last "
            f"FROM source_document WHERE phone IS NOT NULL AND {where} "
            f"GROUP BY phone, coalesce(patient_name, '') ORDER BY max(ingested_at) DESC LIMIT {SEARCH_LIMIT}"), arg).mappings().all()
    merged = _cluster([{"phone": r["phone"], "name": r["name"] or None, "prescriptions": int(r["n"]), "last_uploaded": _iso(r["last"])}
                       for r in rows])
    return [{k: g[k] for k in ("phone", "name", "prescriptions", "last_uploaded")} for g in merged][:SEARCH_LIMIT]


def prescriptions(phone: str, name: str | None = None) -> list[dict[str, Any]]:
    """The prescriptions of one mobile number (and one name, when given), newest first."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 10:
        return []
    sql = ("SELECT id, token_no, original_filename, ingested_at, status, page_count, patient_name, upload_job_id "
           f"FROM source_document WHERE phone = :p ORDER BY ingested_at DESC LIMIT {LIST_LIMIT}")
    with session_scope() as sess:
        rows = sess.execute(text(sql), {"p": digits}).mappings().all()
    if name is not None:                                  # the patient asked for: the names alike to it on this number
        rows = [r for r in rows if same_patient(r["patient_name"], name or None)]
    return [{"document_id": str(r["id"]), "token_no": r["token_no"], "filename": r["original_filename"],
             "uploaded": _iso(r["ingested_at"]), "status": r["status"], "pages": r["page_count"],
             "patient_name": r["patient_name"], "job_id": r["upload_job_id"]} for r in rows]


def existing(phone: str) -> dict[str, Any]:
    """What is already uploaded for this mobile number, so the screen can say so before another one is added."""
    rows = [r for r in prescriptions(phone) if r["status"] not in ("error", "quality_hold")]     # a failed read is not "uploaded"
    return {"count": len(rows), "prescriptions": rows[:20]}
