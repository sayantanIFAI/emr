"""Listener health (LS-S9) and the file-name lookup (LS-S8).

``health()`` answers the questions a person asks: when did it last poll well, how many files wait, how
many are in error or quarantine, how old is the oldest waiting file, and has it stopped (and why).
``stalled`` is true when no poll has succeeded for ``CDI_LISTENER_STALL_SECONDS`` (300: an ASSUMPTION, the
owner sets the number).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ..config import settings


def health(sess: Any, connector: str | None = None) -> dict[str, Any]:
    c = connector or settings.listener_connector
    hb = sess.execute(text(
        "SELECT worker, last_poll_at, last_poll_ok_at, last_error, stopped_reason, "
        "extract(epoch FROM now() - last_poll_ok_at) AS since_ok FROM listener_heartbeat WHERE connector = :c"),
        {"c": c}).mappings().first()
    counts = dict(sess.execute(text(
        "SELECT state, count(*) FROM listener_file WHERE connector = :c GROUP BY state"), {"c": c}).all())
    oldest = sess.execute(text(
        "SELECT extract(epoch FROM now() - min(first_seen_at)) FROM listener_file "
        "WHERE connector = :c AND state = 'seen'"), {"c": c}).scalar_one()
    ignored = sess.execute(text("SELECT count(*) FROM listener_ignored WHERE connector = :c"), {"c": c}).scalar_one()
    since_ok = float(hb["since_ok"]) if hb and hb["since_ok"] is not None else None
    stopped = hb["stopped_reason"] if hb else None
    return {
        "connector": c,
        "last_good_poll_at": hb["last_poll_ok_at"].isoformat() if hb and hb["last_poll_ok_at"] else None,
        "seconds_since_last_good_poll": None if since_ok is None else round(since_ok),
        "files_waiting": int(counts.get("seen", 0)),
        "files_processing": int(counts.get("processing", 0)),
        "files_in_error": int(counts.get("error", 0)),
        "files_in_quarantine": int(counts.get("quarantine", 0)),
        "files_completed": int(counts.get("completed", 0)),
        "files_ignored": int(ignored),
        "oldest_waiting_age_seconds": None if oldest is None else round(float(oldest)),
        "last_error": hb["last_error"] if hb else None,
        "stopped_reason": stopped,
        "stalled": bool(stopped) or since_ok is None or since_ok > settings.listener_stall_seconds,
        "stall_after_seconds": settings.listener_stall_seconds,
    }


def find_documents(sess: Any, name: str, limit: int = 50) -> list[dict[str, Any]]:
    """Documents whose uploaded or dropped file name contains ``name`` (case-insensitive), newest first."""
    esc = name.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    rows = sess.execute(text(
        "SELECT d.id, d.original_filename, d.status, d.source_channel, lf.name AS dropped_file_name, "
        "lf.connector AS drive FROM source_document d LEFT JOIN listener_file lf ON lf.document_id = d.id "
        "WHERE d.original_filename ILIKE :p ESCAPE '!' OR lf.name ILIKE :p ESCAPE '!' "
        "ORDER BY d.ingested_at DESC LIMIT :n"), {"p": f"%{esc}%", "n": limit}).mappings().all()
    return [{"document_id": str(r["id"]), "filename": r["original_filename"], "status": r["status"],
             "channel": r["source_channel"], "dropped_file_name": r["dropped_file_name"], "drive": r["drive"],
             "result": f"api/documents/{r['id']}/result.json"} for r in rows]
