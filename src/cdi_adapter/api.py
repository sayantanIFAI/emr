from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse

from . import __version__, repo, storage
from .db import ping as db_ping
from .db import session_scope
from .ingest.service import ingest_bytes
from .logging import get_logger
from .storage import ping as s3_ping

log = get_logger(__name__)

app = FastAPI(
    title="CDI-Adapter",
    version=__version__,
    summary="Clinical Document Intelligence -> EMR/EHR adapter (ingestion API)",
)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    checks = {"db": db_ping(), "s3": s3_ping()}
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks, "version": __version__},
    )


@app.post("/ingest", status_code=201)
def ingest(
    file: UploadFile = File(...),
    legacy_ref: str | None = Form(default=None),
    legacy_patient_ref: str | None = Form(default=None),
    source_channel: str = Form(default="api"),
) -> dict[str, Any]:
    raw = file.file.read()
    request_id = str(uuid.uuid4())
    try:
        result = ingest_bytes(
            raw,
            filename=file.filename,
            mime_type=file.content_type,
            source_channel=source_channel,
            legacy_ref=legacy_ref,
            legacy_patient_ref=legacy_patient_ref,
            request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "document_id": result.document_id,
        "sha256": result.sha256,
        "deduplicated": result.deduplicated,
        "page_count": result.page_count,
        "status": result.status,
        "request_id": request_id,
    }


@app.get("/documents/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        pages = repo.list_document_pages(sess, document_id)
    return {
        "document": _jsonable(doc),
        "pages": [_jsonable(p) for p in pages],
    }


@app.get("/documents/{document_id}/classification")
def get_classification(document_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        if not repo.get_document(sess, document_id):
            raise HTTPException(status_code=404, detail="document not found")
        cls = repo.get_doc_classification(sess, document_id)
    if not cls:
        raise HTTPException(status_code=404, detail="not classified yet")
    return _jsonable(cls)


@app.get("/documents/{document_id}/ocr")
def get_ocr(document_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        if not repo.get_document(sess, document_id):
            raise HTTPException(status_code=404, detail="document not found")
        blocks = repo.list_ocr_blocks(sess, document_id)
    return {"document_id": document_id, "block_count": len(blocks),
            "blocks": [_jsonable(b) for b in blocks]}


@app.get("/documents/{document_id}/evidence")
def get_evidence(document_id: str) -> dict[str, Any]:
    """Everything a reviewer UI needs: pages + classification + OCR blocks with bboxes."""
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        pages = repo.list_document_pages(sess, document_id)
        cls = repo.get_doc_classification(sess, document_id)
        blocks = repo.list_ocr_blocks(sess, document_id)
    by_page: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page.setdefault(b["page_no"], []).append(_jsonable(b))
    return {
        "document": _jsonable(doc),
        "classification": _jsonable(cls) if cls else None,
        "pages": [
            {**_jsonable(p), "blocks": by_page.get(p["page_no"], [])} for p in pages
        ],
    }


@app.get("/documents/{document_id}/pages/{page_no}")
def get_page_image(document_id: str, page_no: int) -> Response:
    with session_scope() as sess:
        pages = {p["page_no"]: p for p in repo.list_document_pages(sess, document_id)}
    if page_no not in pages:
        raise HTTPException(status_code=404, detail="page not found")
    key = storage.key_from_uri(pages[page_no]["image_uri"])
    return Response(content=storage.get_bytes(key), media_type="image/png")


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[k] = str(v) if isinstance(v, uuid.UUID) else v
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out
