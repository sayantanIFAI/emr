"""File listener (E16): OneDrive / SharePoint / Google Drive / local folder -> full pipeline.

The drive is chosen by configuration only (``CDI_LISTENER_CONNECTOR``, see connectors.py).

Lifecycle of one file version (``listener_file`` row, unique on connector+id+etag):

    inbox ──(unchanged for N polls)──► BATCH of up to 3 ──► claim (lease) ──► processing/ ──► pipeline
       │                                                                           │
       │                                                       ok ────────────────┴──► success/
       │                                                       data error ────────────► error/
       │                                                                                (no retry:
       │                                                                                 rescan etc.)
       │                                                       other error ───────────► error/
       │                                                                                recovery agent
       │                                                                                retries <=3x,
       │                                                                                then quarantine/
       └── every failed run writes ONE reason note into log/  (successes write nothing there)

Batching: the listener takes up to ``CDI_LISTENER_BATCH_SIZE`` (3) stable files together and
runs them concurrently as one batch; the next batch starts when the whole batch is done. If
fewer files are waiting, the partial batch is flushed after ``CDI_LISTENER_BATCH_WAIT_SECONDS``.

Guarantees: a file is processed only after its size/etag stops changing (no half
uploads); one worker holds a lease per file (crash -> lease expires -> recovered);
re-dropping identical bytes dedupes on sha256 in ingest; every transition is appended
to ``listener_file.history``; a file moves to success/ only after its pipeline run is
durably recorded; SIGTERM drains the running batch and exits; nothing is deleted from the drive.

Run:  python -m cdi_adapter.listener.service [--once | --check | --login]
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from .connectors import (FOLDERS, AuthRequired, Connector, RemoteFile, drain_ignored, folder_name,
                         get_connector, reset_ignored, sha256)

log = get_logger(__name__)
WORKER = f"listener@{socket.gethostname()}"
_STOP = threading.Event()


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
    reset_ignored()
    files = conn.list("inbox")
    ignored = drain_ignored()
    with session_scope() as sess:
        for name, reason in ignored.items():      # never silent: a person can see what was skipped and why
            sess.execute(text(
                "INSERT INTO listener_ignored (connector, name, reason) VALUES (:c, :n, :r) "
                "ON CONFLICT (connector, name, reason) DO UPDATE SET last_seen_at = now(), "
                "times_seen = listener_ignored.times_seen + 1"), {"c": conn.name, "n": name, "r": reason})
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
            sess.execute(text("UPDATE listener_file SET stable_polls = LEAST(stable_polls + 1, 1000), "
                              "updated_at = now() WHERE id = :i"), {"i": row["id"]})
            if row["stable_polls"] + 1 >= settings.listener_stable_polls:
                ready.append({**row, "_file": f})
    return ready


def _claim(row_id: Any, state_from: str, batch_id: str | None = None) -> bool:
    with session_scope() as sess:
        n = sess.execute(text(
            "UPDATE listener_file SET state='processing', lease_owner=:w, "
            "lease_until = now() + make_interval(secs => :l), updated_at = now(), "
            "history = history || CAST(:h AS jsonb) "
            "WHERE id = :i AND state = :s"),
            {"w": WORKER, "l": settings.listener_lease_seconds, "i": row_id, "s": state_from,
             "h": _event("claimed", worker=WORKER, batch=batch_id)}).rowcount
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


# PdfReadError: pypdfium2 corrupt / password-protected / zero-sized / oversized PDF (ingest/pages.py);
# its messages do not all contain a data hint, so it is matched by class
_DATA_CLASSES = {"FileDataError", "UnidentifiedImageError", "DecompressionBombError", "EmptyFileError",
                 "PdfReadError"}
_DATA_HINTS = ("failed to open", "cannot open broken", "cannot identify image", "not a pdf",
               "no objects found", "format error", "corrupt", "decode", "unsupported", "truncated")
_TRANSIENT_HINTS = ("timeout", "timed out", "connection", "503", "429", "temporarily", "unavailable")


def _classify_error(exc: BaseException) -> str:
    """data = the file itself is the problem (never retried); transient = the system was
    briefly unavailable (retried); code = anything else (retried, then a person looks)."""
    if isinstance(exc, DataError):
        return "data"
    msg = str(exc).lower()
    if type(exc).__name__ in _DATA_CLASSES:
        return "data"
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(h in msg for h in _TRANSIENT_HINTS):
        return "transient"
    if any(h in msg for h in _DATA_HINTS):                  # e.g. a corrupt-file message
        return "data"
    if isinstance(exc, OSError):
        return "transient"
    return "code"


_QUOTED = re.compile(r"""(['"])(?:(?!\1).){1,400}\1""", re.DOTALL)


def scrub(msg: str, limit: int = 300) -> str:
    """A failure note says why, never what was read: text inside quotes (error messages quote the offending
    value) is blanked. Reasons and technical details stay."""
    return _QUOTED.sub(lambda m: m.group(1) + "..." + m.group(1), msg)[:limit]


def _failure_note(conn: Connector, f: RemoteFile, exc: BaseException, *, klass: str, attempts: int,
                  final: bool, dest: str, batch_id: str | None, delay: int) -> str:
    runs_total = settings.listener_max_attempts + 1
    if klass == "data":
        action = "NO automatic retry: the file itself needs fixing (rescan / re-export)"
    elif final:
        action = f"retries exhausted: parked in {folder_name(dest)}/ for a person to look at"
    else:
        action = (f"automatic retry {attempts + 1} of {settings.listener_max_attempts} "
                  f"in about {delay}s (recovery agent)")
    return (f"time (UTC):    {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}\n"
            f"file:          {f.name}\n"
            f"connector:     {conn.name}\n"
            f"batch:         {batch_id or '-'}\n"
            f"run:           {attempts + 1} of {runs_total} (1 first run + {settings.listener_max_attempts} retries)\n"
            f"error class:   {klass}\n"
            f"reason:        {type(exc).__name__}: {scrub(str(exc))}\n"
            f"moved to:      {folder_name(dest)}/\n"
            f"next:          {action}\n\n"
            f"--- traceback (tail) ---\n{scrub(traceback.format_exc()[-3000:], 3000)}")


def process(conn: Connector, row: dict[str, Any], f: RemoteFile, *, is_retry: bool,
            batch_id: str | None = None) -> str:
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
             _event=_event("completed", batch=batch_id,
                           **{k: v for k, v in result.items() if k != "document_id"},
                           document_id=result.get("document_id")))
        log.info("listener_completed", file=f.name, batch=batch_id, **result)
        return "completed"
    except Exception as exc:  # noqa: BLE001
        klass = _classify_error(exc)
        attempts = int(row.get("attempts") or 0)
        exhausted = is_retry and attempts >= settings.listener_max_attempts
        final = klass == "data" or exhausted
        dest = "quarantine" if exhausted else "error"
        delay = settings.listener_retry_base_seconds * (2 ** attempts)
        remote = f.remote_id
        try:
            moved = conn.move(f, dest)
            remote = moved.remote_id
            # failure reasons go to the log folder ONLY (never next to the file)
            conn.write_note("log", f"{moved.name}.{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"
                                   f".run{attempts + 1}.log",
                            _failure_note(conn, f, exc, klass=klass, attempts=attempts, final=final,
                                          dest=dest, batch_id=batch_id, delay=delay))
        except Exception as mv_exc:  # noqa: BLE001 - leave it where it is; lease expiry recovers
            log.error("listener_move_failed", file=f.name, error=str(mv_exc)[:200])
        _set(rid, state="quarantine" if exhausted else "error", remote_id=remote,
             error_class=klass, last_error=f"{type(exc).__name__}: {scrub(str(exc))}"[:1000],
             next_attempt_at=None if final else _in(delay),
             _event=_event("failed", error_class=klass, to=dest, batch=batch_id, error=str(exc)[:300]))
        log.error("listener_failed", file=f.name, error_class=klass, to=dest, batch=batch_id,
                  error=str(exc)[:200])
        return dest


def _in(seconds: int) -> Any:
    from datetime import timedelta

    return datetime.now(UTC) + timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# batching
# --------------------------------------------------------------------------- #
def select_batch(ready: list[dict[str, Any]], *, flush: bool = False,
                 now: datetime | None = None) -> list[dict[str, Any]]:
    """Pick the next batch: ``batch_size`` stable files (oldest first). A short batch is only
    released once its oldest file has waited ``batch_wait_seconds`` (or ``flush``)."""
    if not ready:
        return []
    ready = sorted(ready, key=lambda r: r["first_seen_at"])
    size = max(1, settings.listener_batch_size)
    if len(ready) >= size:
        return ready[:size]
    now = now or datetime.now(UTC)
    waited = (now - ready[0]["first_seen_at"]).total_seconds()
    return ready if (flush or waited >= settings.listener_batch_wait_seconds) else []


def run_batch(conn: Connector, batch: list[dict[str, Any]]) -> list[str]:
    """Claim every file of the batch, then run them concurrently; return outcomes in order."""
    batch_id = uuid.uuid4().hex[:8]
    claimed = [r for r in batch if _claim(r["id"], "seen", batch_id)]
    if not claimed:
        return []
    log.info("listener_batch_started", batch=batch_id, files=[r["name"] for r in claimed])

    def one(r: dict[str, Any]) -> str:
        try:
            return process(conn, r, r["_file"], is_retry=False, batch_id=batch_id)
        except Exception as exc:  # noqa: BLE001 - lease expiry hands it to the recovery agent
            log.error("listener_batch_item_crashed", file=r["name"], batch=batch_id, error=str(exc)[:200])
            return "crashed"

    with ThreadPoolExecutor(max_workers=len(claimed), thread_name_prefix=f"batch-{batch_id}") as ex:
        results = list(ex.map(one, claimed))
    log.info("listener_batch_finished", batch=batch_id,
             completed=results.count("completed"), error=results.count("error"),
             quarantine=results.count("quarantine"), crashed=results.count("crashed"))
    return results


def poll_once(conn: Connector | None = None, *, flush: bool = False) -> list[str]:
    conn = conn or get_connector()
    batch = select_batch(observe(conn), flush=flush)
    return run_batch(conn, batch) if batch else []


# --------------------------------------------------------------------------- #
def check(conn: Connector) -> dict[str, Any]:
    """Self-test: authenticate, create/resolve every lifecycle folder, count what is in them."""
    conn.ensure_folders()
    return {
        "connector": conn.name,
        "root": settings.listener_root,
        "folders": {f: ("." if folder_name(f).strip() in ("", ".") else folder_name(f)) for f in FOLDERS},
        "files_waiting": {f: len(conn.list(f)) for f in FOLDERS if f != "log"},
        "batch_size": settings.listener_batch_size,
        "batch_wait_seconds": settings.listener_batch_wait_seconds,
        "max_retries": settings.listener_max_attempts,
        "ok": True,
    }


def _beat(conn: Connector, *, ok: bool, error: str | None = None, stopped: str | None = None) -> None:
    """Record the poll (LS-S9): health and the stall alert read this. Never raises."""
    try:
        with session_scope() as sess:
            sess.execute(text(
                "INSERT INTO listener_heartbeat (connector, worker, last_poll_at, last_poll_ok_at, last_error, "
                "stopped_reason, updated_at) VALUES (:c, :w, now(), CASE WHEN :ok THEN now() END, :e, :s, now()) "
                "ON CONFLICT (connector) DO UPDATE SET worker = :w, last_poll_at = now(), "
                "last_poll_ok_at = CASE WHEN :ok THEN now() ELSE listener_heartbeat.last_poll_ok_at END, "
                "last_error = :e, stopped_reason = :s, updated_at = now()"),
                {"c": conn.name, "w": WORKER, "ok": ok, "e": error, "s": stopped})
    except Exception as exc:  # noqa: BLE001
        log.warning("heartbeat_failed", error=str(exc)[:150])


def _install_signal_handlers() -> None:
    def _stop(signum: int, _frame: Any) -> None:
        log.info("listener_draining", signal=signum)
        _STOP.set()
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _stop)
        except ValueError:                    # not the main thread (tests)
            pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description="CDI file listener (local | onedrive | sharepoint | gdrive | pkg.module:Class)")
    ap.add_argument("--once", action="store_true", help="poll once and flush a partial batch "
                    "(stability still needs CDI_LISTENER_STABLE_POLLS polls; use 1 for a drain)")
    ap.add_argument("--check", action="store_true", help="authenticate, create/resolve the folders, "
                    "print what is waiting, and exit (non-zero on failure)")
    ap.add_argument("--login", action="store_true", help="one-time interactive sign-in "
                    "(OneDrive/SharePoint with CDI_GRAPH_AUTH=device_code)")
    a = ap.parse_args()
    from ..security import require_no_default_credentials

    require_no_default_credentials()
    conn = get_connector()
    if a.login:
        conn.login() if hasattr(conn, "login") else print("this connector needs no interactive sign-in")
        return
    if a.check:
        try:
            print(json.dumps(check(conn), indent=2))
        except Exception as exc:
            print(json.dumps({"connector": getattr(conn, "name", "?"), "ok": False,
                              "error": f"{type(exc).__name__}: {exc}"[:500]}, indent=2))
            raise SystemExit(1) from exc
        return
    conn.ensure_folders()
    log.info("listener_started", connector=conn.name, root=settings.listener_root,
             poll=settings.listener_poll_seconds, batch=settings.listener_batch_size, worker=WORKER)
    if a.once:
        print(json.dumps(poll_once(conn, flush=True)))
        return
    _install_signal_handlers()
    _beat(conn, ok=False, error="starting")
    while not _STOP.is_set():
        try:
            poll_once(conn)
            _beat(conn, ok=True)
        except AuthRequired as exc:
            # sign-in is gone: stop with the reason (a person must sign in again) instead of retrying forever
            reason = f"sign-in missing or expired: {scrub(str(exc))}"
            log.error("listener_auth_expired", error=reason)
            _beat(conn, ok=False, error=reason, stopped=reason)
            raise SystemExit(3) from exc
        except Exception as exc:  # noqa: BLE001 - a drive outage must not kill the listener
            log.error("listener_poll_failed", error=scrub(str(exc)))
            _beat(conn, ok=False, error=scrub(str(exc)))
        _STOP.wait(settings.listener_poll_seconds)
    _beat(conn, ok=False, error=None, stopped="stopped by request")
    log.info("listener_stopped")


if __name__ == "__main__":
    main()
