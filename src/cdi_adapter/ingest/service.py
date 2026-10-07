from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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


RETRY_STATUSES = ("quality_hold",)      # same bytes again -> processed again, not answered from the old hold


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


def document_hash(raw: bytes, scope: str | None = None) -> str:
    """The document's identity: the SHA-256 of its bytes (all channels) or, with a scope, of its bytes and that scope."""
    return hashlib.sha256(raw + (b"\x00scope:" + scope.encode("utf-8") if scope else b"")).hexdigest()


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
    dedupe_scope: str | None = None,
) -> IngestResult:
    """Idempotent ingest of one scanned document. Safe to call twice with the same bytes.

    ``dedupe_scope``: a hand-entered identity (the web upload's mobile number and token). The same bytes under another scope are
    a different document, so a photo sent again for another patient never relabels the earlier record."""
    if not raw:
        raise ValueError("empty document")

    sha256 = document_hash(raw, dedupe_scope)
    mime = mime_type or _guess_mime(filename, raw)
    if not _allowed(mime):
        raise ValueError(f"mime_type not allowed: {mime}")

    with session_scope() as sess:
        existing = repo.get_document_by_sha(sess, sha256)
        # a document HELD for rescan is not a result worth returning again: the same bytes may read fine
        # now (a fixed check, a changed setting), and a hold must never become permanent. It is processed
        # again from its stored original; its pages are redone. Anything else is the usual dedupe.
        retry = bool(existing) and existing["status"] in RETRY_STATUSES
        if retry:
            try:
                with sess.begin_nested():
                    sess.execute(text("DELETE FROM document_page WHERE document_id = :d"), {"d": str(existing["id"])})
            except IntegrityError:
                retry = False          # later stages already point at these pages: keep the old answer
        if existing and not retry:
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

        if retry:
            document_id = existing["id"]
            log.info("ingest_reprocess", sha256=sha256, document_id=str(document_id), was=existing["status"])
            repo.set_document_status(sess, document_id, "received", page_count=0, error_detail=None)
            repo.write_audit(
                sess, actor=source_channel, action="update", entity="source_document",
                entity_id=str(document_id), detail={"reprocess": True, "was": existing["status"]},
                request_id=request_id,
            )
        else:
            object_key = f"documents/{sha256[:2]}/{sha256}/original{_ext_for(mime, filename)}"
            storage.put_bytes(object_key, raw, content_type=mime)
            object_uri = storage.object_uri(object_key)

            try:
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
            except IntegrityError:
                # a concurrent upload of the identical bytes won the race - reuse its row
                sess.rollback()
                dup = repo.get_document_by_sha(sess, sha256)
                if not dup:
                    raise
                log.info("ingest_dedup_race", sha256=sha256, document_id=str(dup["id"]))
                return IngestResult(
                    document_id=str(dup["id"]), sha256=sha256, deduplicated=True,
                    page_count=dup["page_count"], status=dup["status"],
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
        held = quality_hold_reasons(rendered)
        with session_scope() as sess:
            for pg in rendered:
                base = f"documents/{sha256[:2]}/{sha256}/pages/{pg.page_no:04d}"
                img_uri = storage.put_bytes(f"{base}.png", pg.png_bytes, "image/png")
                thumb_uri = storage.put_bytes(
                    f"{base}.thumb.png", make_thumbnail(pg.png_bytes), "image/png"
                )
                if pg.src_png:
                    # pixel-faithful render (deskewed, unfiltered) for crops + grounding
                    pg.preproc["src_uri"] = storage.put_bytes(
                        f"{base}.src.png", pg.src_png, "image/png")
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
            status = "quality_hold" if held else "pages_rendered"
            repo.set_document_status(
                sess, document_id, status, page_count=len(rendered),
                error_detail=("rescan: " + " | ".join(held)) if held else None,
            )
            repo.finish_pipeline_run(
                sess, run_id, status="ok",
                metrics={"pages": len(rendered), "quality_hold": bool(held), "quality": held},
            )
            repo.write_audit(
                sess, actor="ingest-svc", action="update", entity="source_document",
                entity_id=str(document_id),
                detail={"pages": len(rendered), "quality_hold": held or None},
            )
        if held:
            log.warning("ingest_quality_hold", document_id=str(document_id), reasons=held)
            return IngestResult(str(document_id), sha256, False, len(rendered), "quality_hold")
        log.info("ingest_ok", document_id=str(document_id), pages=len(rendered))
        _enqueue_next(str(document_id))
        return IngestResult(str(document_id), sha256, False, len(rendered), "pages_rendered")

    except Exception as exc:  # noqa: BLE001
        log.error("ingest_failed", document_id=str(document_id), error=str(exc))
        with session_scope() as sess:
            repo.set_document_status(sess, document_id, "error", error_detail=str(exc))
            repo.finish_pipeline_run(sess, run_id, status="failed", error_detail=str(exc))
        raise


def quality_hold_reasons(rendered: list) -> list[str]:
    """Blocking quality reasons across pages ("p2: image too blurred ..."), or [] to proceed.
    In ``warn`` mode the verdict is recorded on each page but never blocks."""
    if settings.quality_gate_mode != "enforce":
        return []
    out: list[str] = []
    for pg in rendered:
        q = (pg.preproc or {}).get("quality") or {}
        if q and not q.get("passed", True):
            out += [f"page {pg.page_no}: {r}" for r in q.get("reasons", [])]
    return out


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
