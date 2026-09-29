"""Recovery agent (E16-S6): retries failed listener files - at most 3 times.

Picks ``listener_file`` rows that are
  * state = 'error', not a data error, attempts < max, and past their back-off, or
  * state = 'processing' with an expired lease (the worker died mid-file),
moves the file back through processing/ and re-runs the whole pipeline. Each retry
increments ``attempts`` (DB CHECK caps it at 3); after the third failure the file goes
to quarantine/ with a note. Data errors (rescan needed, too big, empty) are never retried.

Run:  python -m cdi_adapter.listener.recovery [--once]
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

from sqlalchemy import text

from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from .connectors import Connector, get_connector
from .service import WORKER, _event, process

log = get_logger(__name__)


def _due() -> list[dict[str, Any]]:
    with session_scope() as sess:
        rows = sess.execute(text(
            """
            UPDATE listener_file SET state='processing', attempts = attempts + 1,
                   lease_owner = :w, lease_until = now() + make_interval(secs => :l),
                   updated_at = now(), history = history || CAST(:h AS jsonb)
             WHERE id IN (
                SELECT id FROM listener_file
                 WHERE attempts < :max AND (
                       (state = 'error' AND coalesce(error_class,'') <> 'data'
                        AND next_attempt_at IS NOT NULL AND next_attempt_at <= now())
                    OR (state = 'processing' AND lease_until < now()))
                 ORDER BY updated_at LIMIT 10 FOR UPDATE SKIP LOCKED)
            RETURNING *
            """), {"w": WORKER, "l": settings.listener_lease_seconds,
                   "max": settings.listener_max_attempts,
                   "h": _event("retry_claimed", worker=WORKER)}).mappings().all()
        return [dict(r) for r in rows]


def _locate(conn: Connector, row: dict[str, Any]):
    for folder in ("error", "processing", "inbox"):
        for f in conn.list(folder):
            if f.remote_id == row["remote_id"] or (conn.name == "local" and f.name == row["name"]
                                                    and folder == "error"):
                return f
    return None


def run_once(conn: Connector | None = None) -> list[dict[str, Any]]:
    conn = conn or get_connector()
    out = []
    for row in _due():
        f = _locate(conn, row)
        if f is None:
            with session_scope() as sess:
                sess.execute(text(
                    "UPDATE listener_file SET state='quarantine', lease_owner=NULL, "
                    "lease_until=NULL, last_error='file no longer present in the drive', "
                    "updated_at=now(), history = history || CAST(:h AS jsonb) WHERE id=:i"),
                    {"i": row["id"], "h": _event("missing")})
            out.append({"id": str(row["id"]), "result": "missing"})
            continue
        res = process(conn, row, f, is_retry=True)
        log.info("listener_retry", file=row["name"], attempt=row["attempts"], result=res)
        out.append({"id": str(row["id"]), "file": row["name"], "attempt": row["attempts"],
                    "result": res})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="listener recovery agent (max 3 retries)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    conn = get_connector()
    if a.once:
        print(json.dumps(run_once(conn), default=str, indent=2))
        return
    while True:
        try:
            run_once(conn)
        except Exception as exc:  # noqa: BLE001
            log.error("recovery_failed", error=str(exc)[:300])
        time.sleep(max(15.0, settings.listener_poll_seconds))


if __name__ == "__main__":
    main()
