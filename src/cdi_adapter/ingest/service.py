from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from .pages import make_thumbnail, render_pages

log = get_logger(__name__)


@dataclass
class IngestResult:
    document_id: str
    sha256: str
    deduplicated: bool
    page_count: int
    status: str


def _pkg_version() -> str:
    from .. import __version__

    return __version__


def _guess_mime(filename: str | None, raw: bytes) -> str:
    if raw[:5] == b"%PDF-":
        return "application/pdf"
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return "application/octet-stream"


def _allowed(mime: str) -> bool:
    return any(mime.startswith(p) for p in settings.allowed_mime_prefixes)


def ingest_bytes(
    raw: bytes,
    *,
    filename: str | None = None,
    mime_type: str | None = None,
    source_channel: str = "api",
    legacy_ref: str | None = None,
    legacy_patient_ref: str | None = None,
    captured_at: datetime | None = None,
    request_id: str | None = None,
) -> IngestResult:
    """Idempotent ingest of one scanned document. Safe to call twice with the same bytes."""
    if not raw:
        raise ValueError("empty document")

    sha256 = hashlib.sha256(raw).hexdigest()
    mime = mime_type or _guess_mime(filename, raw)
    if not _allowed(mime):
        raise ValueError(f"mime_type not allowed: {mime}")

    with session_scope() as sess:
        existing = repo.get_document_by_sha(sess, sha256)
        if existing:
            log.info("ingest_dedup", sha256=sha256, document_id=str(existing["id"]))
            repo.write_audit(
                sess, actor=source_channel, action="read", entity="source_document",
                entity_id=str(existing["id"]), detail={"dedup": True}, request_id=request_id,
            )
            return IngestResult(
                document_id=str(existing["id"]),
                sha256=sha256,
                deduplicated=True,
                page_count=existing["page_count"],
                status=existing["status"],
            )

        object_key = f"documents/{sha256[:2]}/{sha256}/original{_ext_for(mime, filename)}"
        storage.put_bytes(object_key, raw, content_type=mime)
        object_uri = storage.object_uri(object_key)

        document_id = repo.insert_source_document(
            sess,
            sha256=sha256,
            mime_type=mime,
            object_uri=object_uri,
            byte_size=len(raw),
            source_channel=source_channel,
            original_filename=filename,
            legacy_ref=legacy_ref,
            legacy_patient_ref=legacy_patient_ref,
            captured_at=captured_at,
        )
        repo.write_audit(
            sess, actor=source_channel, action="create", entity="source_document",
            entity_id=str(document_id),
            detail={"filename": filename, "mime": mime, "bytes": len(raw)},
            request_id=request_id,
        )

    # Heavy work (rendering) outside the first tx; then persist pages in a new tx.
    with session_scope() as sess:
        run_id = repo.start_pipeline_run(
            sess, document_id=document_id, stage="ingest",
            model_name="page-normalizer", model_version=_pkg_version(),
            params={"dpi": settings.page_dpi, "deskew": settings.deskew_enabled},
            input_sha=sha256,
        )

    try:
        rendered = render_pages(raw, mime)
        with session_scope() as sess:
            for pg in rendered:
                base = f"documents/{sha256[:2]}/{sha256}/pages/{pg.page_no:04d}"
                img_uri = storage.put_bytes(f"{base}.png", pg.png_bytes, "image/png")
                thumb_uri = storage.put_bytes(
                    f"{base}.thumb.png", make_thumbnail(pg.png_bytes), "image/png"
                )
                repo.insert_document_page(
                    sess,
                    document_id=document_id,
                    page_no=pg.page_no,
                    image_uri=img_uri,
                    thumb_uri=thumb_uri,
                    width_px=pg.width_px,
                    height_px=pg.height_px,
                    dpi=pg.dpi,
                    preproc=pg.preproc,
                )
            repo.set_document_status(
                sess, document_id, "pages_rendered", page_count=len(rendered)
            )
            repo.finish_pipeline_run(
                sess, run_id, status="ok", metrics={"pages": len(rendered)},
            )
            repo.write_audit(
                sess, actor="ingest-svc", action="update", entity="source_document",
                entity_id=str(document_id), detail={"pages": len(rendered)},
            )
        log.info("ingest_ok", document_id=str(document_id), pages=len(rendered))
        _enqueue_next(str(document_id))
        return IngestResult(str(document_id), sha256, False, len(rendered), "pages_rendered")

    except Exception as exc:  # noqa: BLE001
        log.error("ingest_failed", document_id=str(document_id), error=str(exc))
        with session_scope() as sess:
            repo.set_document_status(sess, document_id, "error", error_detail=str(exc))
            repo.finish_pipeline_run(sess, run_id, status="failed", error_detail=str(exc))
        raise


def _ext_for(mime: str, filename: str | None) -> str:
    if filename and "." in PurePosixPath(filename).name:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/tiff": ".tif",
    }.get(mime, ".bin")


def _enqueue_next(document_id: str) -> None:
    """Hand off to stage S2 (classification). Best-effort: never fail ingest on a broker hiccup."""
    try:
        from ..worker import classify_document  # local import: avoids celery import at module load

        classify_document.delay(document_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("enqueue_classify_skipped", document_id=document_id, error=str(exc))
