from __future__ import annotations

import json
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .. import __version__, repo, storage
from ..config import settings
from ..db import ping as db_ping
from ..db import session_scope
from ..fhir.service import project_document, project_patient
from ..logging import get_logger
from ..ml.client import get_client
from ..storage import ping as s3_ping
from . import review as review_svc
from .jobs import apply_edits_and_generate, create_job, get_job, job_facts
from .page import PAGE
from .review_page import REVIEW_PAGE
from .reviewer import router as reviewer_router
from .reviewer_page import REVIEWER_PAGE

log = get_logger(__name__)
app = FastAPI(title="CDI-Adapter - scanned docs -> ABDM FHIR", version=__version__)
app.include_router(reviewer_router)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/healthz")
def healthz() -> Any:
    checks = {"db": db_ping(), "s3": s3_ping()}
    try:
        checks["mlserve"] = get_client().healthz().get("status") == "ok"
    except Exception:  # noqa: BLE001
        checks["mlserve"] = False
    ok = checks["db"] and checks["s3"]
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded",
                                 "checks": checks, "version": __version__,
                                 "ig_package": settings.ig_package})


@app.get("/api/registry/lookup")
def registry_lookup(q: str) -> dict[str, Any]:
    """Resolve an existing clinic patient by CareFlow id / ABHA id / mobile."""
    from ..mpi import registry
    with session_scope() as sess:
        row = registry.lookup(sess, q)
    return {"found": row is not None, "patient": row}


@app.post("/api/registry")
def registry_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist a new patient's full details for future look-ups."""
    from ..mpi import registry
    req = {k: (body or {}).get(k) for k in
           ("patient_id", "name", "mobile", "dob", "gender", "address", "abha_id")}
    if not req["name"] or not req["mobile"]:
        raise HTTPException(422, "name and mobile are required")
    with session_scope() as sess:
        row = registry.save(sess, **req)
    return {"ok": True, "patient": row}


@app.post("/api/jobs", status_code=202)
async def submit_job(
    abha: str | None = Form(default=None),
    patient_ref: str | None = Form(default=None),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """If ``patient_ref`` (CareFlow id / ABHA / mobile) matches the clinic
    registry, all documents attach to that patient. Otherwise the patient's name,
    sex and DOB are read from the documents and a new CareFlow id is generated."""
    if not files:
        raise HTTPException(422, "attach at least one document")
    if len(files) > 10:
        raise HTTPException(422, "max 10 documents per patient")
    payload: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if data:
            payload.append((f.filename or "document", data))
    if not payload:
        raise HTTPException(422, "all uploads were empty")
    jid = create_job(abha, payload, patient_ref=patient_ref)
    return {"job_id": jid, "documents": len(payload)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job.public()


@app.get("/api/jobs/{job_id}/facts")
def job_facts_endpoint(job_id: str) -> Any:
    """Extracted facts per document for the inline human editor (image left, table right)."""
    try:
        return JSONResponse(job_facts(job_id))
    except KeyError:
        raise HTTPException(404, "unknown job or no patient resolved yet") from None


@app.post("/api/jobs/{job_id}/generate")
def job_generate(job_id: str, body: dict[str, Any] = Body(default={})) -> Any:
    """Apply the editor's keep/edit/drop decisions, then build the FHIR bundles (ms)."""
    edits = (body or {}).get("edits") or []
    reviewer = (body or {}).get("reviewer") or "reviewer"
    try:
        return JSONResponse(apply_edits_and_generate(job_id, edits, reviewer))
    except KeyError:
        raise HTTPException(404, "unknown job") from None


@app.get("/api/jobs/{job_id}/fhir")
def job_fhir(job_id: str) -> Any:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if not job.patient_id:
        raise HTTPException(409, f"job not started (state={job.state})")
    # re-project live so it reflects any review decisions since the job finished
    return JSONResponse(project_patient(job.patient_id))


@app.get("/api/jobs/{job_id}/fhir/download")
def job_fhir_download(job_id: str) -> Response:
    job = get_job(job_id)
    if not job or not job.patient_id:
        raise HTTPException(404, "no result")
    body = json.dumps(project_patient(job.patient_id), indent=2, default=str)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="fhir_{job_id}.json"'},
    )


# --------------------------------------------------------------------------- #
# S8 human review
# --------------------------------------------------------------------------- #
@app.get("/review", response_class=HTMLResponse)
def review_index() -> str:
    return REVIEW_PAGE


@app.get("/reviewer", response_class=HTMLResponse)
def reviewer_index() -> str:
    return REVIEWER_PAGE


@app.get("/api/review/tasks")
def review_tasks(patient_id: str | None = None) -> dict[str, Any]:
    return {"tasks": review_svc.list_open_tasks(patient_id)}


@app.get("/api/review/facts/{fact_id}")
def review_fact(fact_id: str) -> dict[str, Any]:
    try:
        return review_svc.fact_detail(fact_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/facts/{fact_id}/review")
def review_decision(fact_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    action = (body or {}).get("action", "")
    try:
        return review_svc.submit_decision(
            fact_id, action, reviewer=body.get("reviewer") or "reviewer",
            corrections=body.get("corrections"), note=body.get("note"),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/documents/{document_id}/original")
def document_original(document_id: str) -> Response:
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    key = storage.key_from_uri(doc["object_uri"])
    return Response(content=storage.get_bytes(key),
                    media_type=doc.get("mime_type") or "application/pdf")


@app.get("/api/documents/{document_id}/pages/{page_no}")
def document_page_image(document_id: str, page_no: int) -> Response:
    with session_scope() as sess:
        pages = {p["page_no"]: p for p in repo.list_document_pages(sess, document_id)}
    if page_no not in pages:
        raise HTTPException(404, "page not found")
    key = storage.key_from_uri(pages[page_no]["image_uri"])
    return Response(content=storage.get_bytes(key), media_type="image/png")


@app.get("/api/patients/{patient_id}/fhir")
def patient_fhir(patient_id: str) -> Any:
    try:
        return JSONResponse(project_patient(patient_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/documents/{document_id}/bundle")
def document_bundle(document_id: str) -> Any:
    try:
        return JSONResponse(project_document(document_id, persist=False)["bundle"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/documents/{document_id}/evidence")
def document_evidence(document_id: str) -> dict[str, Any]:
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise HTTPException(404, "document not found")
        cls = repo.get_doc_classification(sess, document_id)
        blocks = repo.list_ocr_blocks(sess, document_id)
        facts = repo.list_clinical_facts(sess, document_id=document_id)
    return {
        "document_id": document_id,
        "doc_type": (cls or {}).get("doc_type"),
        "ocr_block_count": len(blocks),
        "facts": [
            {"type": f["fact_type"], "text": f["local_text"],
             "code_system": f["code_system"], "code": f["code"],
             "code_display": f["code_display"], "code_status": f["code_status"],
             "value_num": float(f["value_num"]) if f["value_num"] is not None else None,
             "unit": f["value_unit_ucum"], "flag": f["abnormal_flag"],
             "confidence": float(f["confidence_overall"])}
            for f in facts
        ],
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.webapp_port,
                log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
