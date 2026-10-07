"""Finding patients and their prescriptions on the upload screen (no dropdown of thousands: type a part of the mobile number).

A patient here is a (mobile number, name) pair, as the front desk sees it: the mobile number is typed with the upload, the
name is read from the page. Two people who share a phone are two patients. Everything is plain SQL on ``source_document``
(migration 0010); results are capped, so a search never returns thousands of rows.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from ..db import session_scope

SEARCH_LIMIT = 10
LIST_LIMIT = 100


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
    return [{"phone": r["phone"], "name": r["name"] or None, "prescriptions": int(r["n"]), "last_uploaded": _iso(r["last"])}
            for r in rows]


def prescriptions(phone: str, name: str | None = None) -> list[dict[str, Any]]:
    """The prescriptions of one mobile number (and one name, when given), newest first."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 10:
        return []
    sql = ("SELECT id, token_no, original_filename, ingested_at, status, page_count, patient_name, upload_job_id "
           "FROM source_document WHERE phone = :p")
    args: dict[str, Any] = {"p": digits}
    if name is not None:
        sql += " AND coalesce(patient_name, '') = :n"
        args["n"] = name
    sql += f" ORDER BY ingested_at DESC LIMIT {LIST_LIMIT}"
    with session_scope() as sess:
        rows = sess.execute(text(sql), args).mappings().all()
    return [{"document_id": str(r["id"]), "token_no": r["token_no"], "filename": r["original_filename"],
             "uploaded": _iso(r["ingested_at"]), "status": r["status"], "pages": r["page_count"],
             "patient_name": r["patient_name"], "job_id": r["upload_job_id"]} for r in rows]


def existing(phone: str) -> dict[str, Any]:
    """What is already uploaded for this mobile number, so the screen can say so before another one is added."""
    rows = [r for r in prescriptions(phone) if r["status"] not in ("error", "quality_hold")]     # a failed read is not "uploaded"
    return {"count": len(rows), "prescriptions": rows[:20]}
