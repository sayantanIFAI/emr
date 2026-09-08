"""Folder-watch connector (the chosen first-slice legacy intake).

Watches ``CDI_INBOX_DIR`` for scanned files dropped by the hospital's scanner or a
legacy export job. An optional JSON sidecar (``<file>.json``) carries legacy
metadata::

    { "legacy_ref": "...", "legacy_patient_ref": "MRN123",
      "captured_at": "2024-03-12T10:15:00+05:30" }

Processed files move to ``CDI_PROCESSED_DIR``; failures move to ``CDI_FAILED_DIR``
with a ``.error.txt`` note. Idempotent: re-dropping the same bytes dedupes on sha256.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..config import settings
from ..logging import get_logger
from .service import ingest_bytes

log = get_logger(__name__)

_SIDECAR_SUFFIX = ".json"
_IGNORE_SUFFIXES = (_SIDECAR_SUFFIX, ".part", ".tmp", ".crdownload", ".error.txt")


def _dirs() -> tuple[Path, Path, Path]:
    inbox = Path(settings.inbox_dir)
    processed = Path(settings.processed_dir)
    failed = Path(settings.failed_dir)
    for d in (inbox, processed, failed):
        d.mkdir(parents=True, exist_ok=True)
    return inbox, processed, failed


def _stable(path: Path, settle: float) -> bool:
    try:
        s1 = path.stat().st_size
        time.sleep(settle)
        return path.exists() and path.stat().st_size == s1
    except FileNotFoundError:
        return False


def _read_sidecar(path: Path) -> dict:
    sc = path.with_suffix(path.suffix + _SIDECAR_SUFFIX)
    if not sc.exists():
        sc = path.with_suffix(_SIDECAR_SUFFIX)
    if sc.exists():
        try:
            return json.loads(sc.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("sidecar_unreadable", file=str(sc), error=str(exc))
    return {}


def process_file(path: Path) -> None:
    inbox, processed, failed = _dirs()
    if path.suffix.lower() in _IGNORE_SUFFIXES or not path.is_file():
        return
    if not _stable(path, settings.watch_settle_seconds):
        log.debug("file_not_stable_yet", file=str(path))
        return

    meta = _read_sidecar(path)
    captured_at = None
    if meta.get("captured_at"):
        try:
            captured_at = datetime.fromisoformat(meta["captured_at"])
        except ValueError:
            pass

    try:
        raw = path.read_bytes()
        result = ingest_bytes(
            raw,
            filename=path.name,
            source_channel="folder_watch",
            legacy_ref=meta.get("legacy_ref"),
            legacy_patient_ref=meta.get("legacy_patient_ref"),
            captured_at=captured_at,
        )
        log.info(
            "watch_ingested",
            file=path.name,
            document_id=result.document_id,
            dedup=result.deduplicated,
            pages=result.page_count,
        )
        _move(path, processed)
        for sc in (path.with_suffix(path.suffix + _SIDECAR_SUFFIX), path.with_suffix(_SIDECAR_SUFFIX)):
            if sc.exists():
                _move(sc, processed)
    except Exception as exc:  # noqa: BLE001
        log.error("watch_ingest_failed", file=path.name, error=str(exc))
        dest = _move(path, failed)
        if dest:
            dest.with_suffix(dest.suffix + ".error.txt").write_text(str(exc), encoding="utf-8")


def _move(path: Path, dest_dir: Path) -> Path | None:
    try:
        dest = dest_dir / path.name
        if dest.exists():
            dest = dest_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
        shutil.move(str(path), str(dest))
        return dest
    except FileNotFoundError:
        return None


def scan_once() -> int:
    """Process everything currently in the inbox. Returns count handled."""
    inbox, _, _ = _dirs()
    n = 0
    for p in sorted(inbox.iterdir()):
        if p.is_file() and p.suffix.lower() not in _IGNORE_SUFFIXES:
            process_file(p)
            n += 1
    return n


class _Handler(FileSystemEventHandler):
    def on_created(self, event) -> None:  # noqa: ANN001
        if not event.is_directory:
            process_file(Path(event.src_path))

    def on_moved(self, event) -> None:  # noqa: ANN001
        if not event.is_directory:
            process_file(Path(event.dest_path))


def main() -> None:
    inbox, processed, failed = _dirs()
    log.info("watcher_start", inbox=str(inbox), processed=str(processed), failed=str(failed))
    scan_once()  # drain anything already present
    obs = Observer()
    obs.schedule(_Handler(), str(inbox), recursive=False)
    obs.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("watcher_stop")
    finally:
        obs.stop()
        obs.join()


if __name__ == "__main__":
    main()
