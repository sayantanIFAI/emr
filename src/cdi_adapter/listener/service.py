"""File listener (E16): OneDrive / SharePoint / local folder -> full pipeline.

Lifecycle of one file version (``listener_file`` row, unique on connector+id+etag):

    inbox ──(unchanged for N polls)──► claim (lease) ──► processing/ ──► pipeline
       │                                                      │
       │                                         ok ──────────┴──► completed/
       │                                         data error ──────► error/  + .error.txt
       │                                                            (no retry: rescan etc.)
       │                                         other error ─────► error/  + .error.txt
       │                                                            recovery agent retries
       │                                                            up to 3 times, then
       └── too big / empty / unsupported ─────────────────────────► quarantine/

Guarantees: a file is processed only after its size/etag stops changing (no half
uploads); one worker holds a lease per file (crash -> lease expires -> recovered);
re-dropping identical bytes dedupes on sha256 in ingest; every transition is appended
to ``listener_file.history``; nothing is ever deleted from the drive.
"""
from __future__ import annotations

import argparse
import json
import socket
import time
import traceback
from typing import Any

from sqlalchemy import text

from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from .connectors import Connector, RemoteFile, get_connector, sha256

log = get_logger(__name__)
WORKER = f"listener@{socket.gethostname()}"


class DataError(Exception):
    """The file itself is the problem (unreadable, quality hold, too big) - retrying the
    same bytes cannot help; it needs a person (rescan / fix the export)."""


def _event(kind: str, **kw: Any) -> str:
    return json.dumps([{"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **kw}],
                      default=str)


def _row(sess: Any, connector: str, f: RemoteFile) -> dict[str, Any] | None:
    r = sess.execute(text("SELECT * FROM listener_file WHERE connector=:c AND remote_id=:r "
                          "AND etag=:e"), {"c": connector, "r": f.remote_id, "e": f.etag}
                     ).mappings().first()
    return dict(r) if r else None


def observe(conn: Connector) -> list[dict[str, Any]]:
    """One poll of the inbox: record new versions, count stable polls, return the rows
    that are ready (stable and unclaimed)."""
    ready: list[dict[str, Any]] = []
    files = conn.list("inbox")
    with session_scope() as sess:
        for f in files:
            row = _row(sess, conn.name, f)
            if row is None:
                sess.execute(text(
                    "INSERT INTO listener_file (connector, remote_id, name, etag, byte_size, "
                    "stable_polls, history) VALUES (:c, :r, :n, :e, :s, 1, CAST(:h AS jsonb)) "
                    "ON CONFLICT DO NOTHING"),
                    {"c": conn.name, "r": f.remote_id, "n": f.name, "e": f.etag, "s": f.size,
                     "h": _event("seen", size=f.size)})
                # an earlier version of the same file that never stabilised
                sess.execute(text("DELETE FROM listener_file WHERE connector=:c AND remote_id=:r "
                                  "AND etag<>:e AND state='seen'"),
                             {"c": conn.name, "r": f.remote_id, "e": f.etag})
                continue
            if row["state"] != "seen":
                continue
            sess.execute(text("UPDATE listener_file SET stable_polls = stable_polls + 1, "
                              "updated_at = now() WHERE id = :i"), {"i": row["id"]})
            if row["stable_polls"] + 1 >= settings.listener_stable_polls:
                ready.append({**row, "_file": f})
    return ready


def _claim(row_id: Any, state_from: str) -> bool:
    with session_scope() as sess:
        n = sess.execute(text(
            "UPDATE listener_file SET state='processing', lease_owner=:w, "
            "lease_until = now() + make_interval(secs => :l), updated_at = now(), "
            "history = history || CAST(:h AS jsonb) "
            "WHERE id = :i AND state = :s"),
            {"w": WORKER, "l": settings.listener_lease_seconds, "i": row_id, "s": state_from,
             "h": _event("claimed", worker=WORKER)}).rowcount
    return n == 1


def _set(row_id: Any, **cols: Any) -> None:
    ev = cols.pop("_event")
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with session_scope() as sess:
        sess.execute(text(f"UPDATE listener_file SET {sets}{',' if sets else ''} updated_at = now(), "
                          "lease_owner = NULL, lease_until = NULL, "
                          "history = history || CAST(:_h AS jsonb) WHERE id = :_i"),
                     {**cols, "_h": ev, "_i": row_id})


def run_pipeline(raw: bytes, filename: str, *, source_channel: str = "listener") -> dict[str, Any]:
    """ingest -> OCR -> classify -> recognition -> extract -> bind -> validate (-> FHIR agent)."""
    from ..ingest.service import ingest_bytes

    res = ingest_bytes(raw, filename=filename, source_channel=source_channel)
    doc = res.document_id
    if res.status == "quality_hold":
        from .. import repo
        with session_scope() as sess:
            d = repo.get_document(sess, doc) or {}
        raise DataError(d.get("error_detail") or "rescan: image quality below threshold")
    if settings.listener_pipeline == "celery":
        return {"document_id": doc, "dedup": res.deduplicated, "mode": "celery"}
    if res.deduplicated and res.status in ("validated", "projected"):
        return {"document_id": doc, "dedup": True, "status": res.status}

    from ..classify.service import classify_document
    from ..extract.service import extract_document
    from ..ocr.service import ocr_document
    from ..terminology.service import bind_document
    from ..validate.service import validate_document

    ocr_document(doc, force_engine="rapidocr")
    c = classify_document(doc)
    if settings.recognition_v2 or (c.is_handwritten and settings.handwritten_uses_vlm):
        ocr_document(doc, force_engine="vlm")
    ex = extract_document(doc)
    bind_document(doc)
    v = validate_document(doc)
    return {"document_id": doc, "doc_type": c.doc_type, "facts": ex.n_facts,
            "auto_accepted": v.auto_accepted, "in_review": v.in_review}


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, DataError):
        return "data"
    msg = str(exc).lower()
    if isinstance(exc, (ValueError,)) and ("decode" in msg or "unsupported" in msg or "pdf" in msg):
        return "data"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or "timeout" in msg \
            or "connection" in msg or "503" in msg or "429" in msg:
        return "transient"
    return "code"


def process(conn: Connector, row: dict[str, Any], f: RemoteFile, *, is_retry: bool) -> str:
    """Run one claimed file through the pipeline and file it by outcome."""
    rid = row["id"]
    try:
        f = conn.move(f, "processing")
        _set(rid, remote_id=f.remote_id, _event=_event("moved", to="processing"))
        if f.size > settings.listener_max_bytes:
            raise DataError(f"file is {f.size} bytes (max {settings.listener_max_bytes})")
        raw = conn.download(f)
        if not raw:
            raise DataError("file is empty")
        result = run_pipeline(raw, f.name)
        done = conn.move(f, "completed")
        _set(rid, state="completed", remote_id=done.remote_id, sha256=sha256(raw),
             document_id=result.get("document_id"), last_error=None, error_class=None,
             _event=_event("completed", **{k: v for k, v in result.items() if k != "document_id"},
                           document_id=result.get("document_id")))
        log.info("listener_completed", file=f.name, **result)
        return "completed"
    except Exception as exc:  # noqa: BLE001
        klass = _classify_error(exc)
        attempts = int(row.get("attempts") or 0)
        final = klass == "data" or (is_retry and attempts >= settings.listener_max_attempts)
        dest = "quarantine" if (is_retry and attempts >= settings.listener_max_attempts) else "error"
        note = (f"file: {f.name}\nconnector: {conn.name}\nerror_class: {klass}\n"
                f"attempts (agent retries): {attempts}/{settings.listener_max_attempts}\n"
                f"error: {type(exc).__name__}: {exc}\n\n"
                + ("no automatic retry - " + ("needs a rescan / fixed file" if klass == "data"
                                              else "retries exhausted") if final
                   else "the recovery agent will retry automatically") + "\n\n"
                + traceback.format_exc()[-3000:])
        try:
            moved = conn.move(f, dest)
            conn.write_note(dest, moved.name + ".error.txt", note)
            remote = moved.remote_id
        except Exception as mv_exc:  # noqa: BLE001 - leave it where it is; lease expiry recovers
            log.error("listener_move_failed", file=f.name, error=str(mv_exc)[:200])
            remote = f.remote_id
        delay = settings.listener_retry_base_seconds * (2 ** attempts)
        _set(rid, state="quarantine" if dest == "quarantine" else "error", remote_id=remote,
             error_class=klass, last_error=f"{type(exc).__name__}: {exc}"[:1000],
             next_attempt_at=None if final else _in(delay),
             _event=_event("failed", error_class=klass, to=dest, error=str(exc)[:300]))
        log.error("listener_failed", file=f.name, error_class=klass, to=dest,
                  error=str(exc)[:200])
        return dest


def _in(seconds: int) -> Any:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def poll_once(conn: Connector | None = None) -> list[str]:
    conn = conn or get_connector()
    out = []
    for row in observe(conn):
        if _claim(row["id"], "seen"):
            out.append(process(conn, row, row["_file"], is_retry=False))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="CDI file listener (local | onedrive | sharepoint)")
    ap.add_argument("--once", action="store_true", help="poll once (stability still needs "
                    "CDI_LISTENER_STABLE_POLLS polls; set it to 1 for a one-shot drain)")
    a = ap.parse_args()
    conn = get_connector()
    conn.ensure_folders()
    log.info("listener_started", connector=conn.name, root=settings.listener_root,
             poll=settings.listener_poll_seconds, worker=WORKER)
    if a.once:
        print(json.dumps(poll_once(conn)))
        return
    while True:
        try:
            poll_once(conn)
        except Exception as exc:  # noqa: BLE001 - a drive outage must not kill the listener
            log.error("listener_poll_failed", error=str(exc)[:300])
        time.sleep(settings.listener_poll_seconds)


if __name__ == "__main__":
    main()
