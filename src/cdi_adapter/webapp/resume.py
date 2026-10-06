"""Pick up what a restart interrupted (OUT-S3).

A web upload runs in memory threads, so a restart (deploy, crash, pod move) used to leave its documents
half done. The original bytes are saved first (object store) and every stage can run again on the same
document (the same path the listener's retry uses), so on start the web app finds the documents of the
``webapp`` channel that never reached a final state and runs them again:

* each pick-up counts (``resume_attempts``); after ``CDI_RESUME_MAX_ATTEMPTS`` the document is parked as
  an error with the reason, so one poison document cannot loop forever and never blocks the others;
* one document failing never stops the others;
* running a finished document again changes nothing (the same bytes are the same document).

Assumption: one web app process per database. A second instance starting while the first is mid-way
through a document would run it twice (the result is still one document, but the work is duplicated).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .. import storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger

log = get_logger(__name__)

FINAL = ("validated", "projected", "normalized", "error", "quality_hold")


def _claim() -> tuple[list[dict[str, Any]], int]:
    """Claim the unfinished web documents (counting the attempt) and park the ones past the cap."""
    final = list(FINAL)
    with session_scope() as sess:
        parked = sess.execute(text(
            "UPDATE source_document SET status = 'error', error_detail = :why "
            "WHERE source_channel = 'webapp' AND status <> ALL(:final) AND resume_attempts >= :cap"),
            {"why": f"stopped: interrupted and picked up again {settings.resume_max_attempts} times without "
                    "finishing. Send it again or ask for help",
             "final": final, "cap": settings.resume_max_attempts}).rowcount
        rows = sess.execute(text(
            "UPDATE source_document SET resume_attempts = resume_attempts + 1 WHERE id IN ("
            " SELECT id FROM source_document WHERE source_channel = 'webapp' AND status <> ALL(:final) "
            "  AND resume_attempts < :cap ORDER BY ingested_at LIMIT :n FOR UPDATE SKIP LOCKED) "
            "RETURNING id, original_filename, object_uri, resume_attempts"),
            {"final": final, "cap": settings.resume_max_attempts, "n": settings.resume_batch}).mappings().all()
    return [dict(r) for r in rows], int(parked or 0)


def resume_unfinished() -> list[dict[str, Any]]:
    from ..listener.service import run_pipeline           # the same stage chain the retry agent re-runs

    rows, parked = _claim()
    out: list[dict[str, Any]] = []
    if parked:
        log.warning("resume_parked", documents=parked)
    for r in rows:
        did = str(r["id"])
        try:
            raw = storage.get_bytes(storage.key_from_uri(r["object_uri"]))
            res = run_pipeline(raw, r["original_filename"] or "document", source_channel="webapp")
            log.info("resumed", document_id=did, attempt=r["resume_attempts"], **{
                k: v for k, v in res.items() if k != "document_id"})
            out.append({"document_id": did, "result": "resumed"})
        except Exception as exc:  # noqa: BLE001 - one document never blocks the others
            log.error("resume_failed", document_id=did, attempt=r["resume_attempts"], error=str(exc)[:300])
            try:
                with session_scope() as sess:
                    sess.execute(text(
                        "UPDATE source_document SET status = 'error', error_detail = :e WHERE id = :i "
                        "AND status <> ALL(:final)"),
                        {"e": f"could not be resumed: {exc}"[:400], "i": did, "final": list(FINAL)})
            except Exception:  # noqa: BLE001
                pass
            out.append({"document_id": did, "result": "failed"})
    return out


def start_in_background() -> None:
    """Called when the web app starts; never blocks start-up and never raises."""
    if not settings.resume_on_start:
        return
    import threading

    def run() -> None:
        try:
            done = resume_unfinished()
            if done:
                log.info("resume_done", documents=len(done))
        except Exception as exc:  # noqa: BLE001
            log.error("resume_scan_failed", error=str(exc)[:300])

    threading.Thread(target=run, name="resume", daemon=True).start()
