from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .. import repo
from ..classify.service import classify_document
from ..config import settings
from ..db import session_scope
from ..extract.service import extract_document
from ..fhir.service import project_patient
from ..ingest.service import ingest_bytes
from ..logging import get_logger
from ..mpi.service import (IdentityCandidate, dob_match, merge_identity_evidence,
                           names_match, parse_age, parse_dob)
from ..ocr.service import ocr_document
from ..terminology.service import bind_document
from ..validate.service import validate_document

log = get_logger(__name__)

STAGES = ["ingest", "classify", "ocr", "extract", "terminology", "validate"]

# CPU-bound stages (ingest / fast-classify / OCR) run for every document at once;
# the GPU extract stage is serialised (single VLM), so no document sits "queued".
_pool = ThreadPoolExecutor(max_workers=max(2, settings.job_max_workers),
                           thread_name_prefix="cdi-job")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class DocProg:
    filename: str
    document_id: str | None = None
    doc_type: str | None = None
    status: str = "queued"          # queued|running|done|error
    facts: int = 0
    accepted: int = 0
    in_review: int = 0
    error: str | None = None
    stages: dict[str, str] = field(
        default_factory=lambda: {s: "pending" for s in STAGES})
    t0: float = field(default_factory=time.time)
    seconds: float | None = None

    def stage(self, name: str, state: str) -> None:
        self.stages[name] = state


@dataclass
class Job:
    id: str
    abha: str | None
    patient_id: str | None = None
    patient: dict[str, Any] | None = None
    existing: bool = False         # matched an existing registry patient
    state: str = "queued"          # queued|running|review|generating|done|error|mismatch
    created: float = field(default_factory=time.time)
    docs: list[DocProg] = field(default_factory=list)
    error: str | None = None
    mismatch: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        pat = self.patient or (self.result or {}).get("patient")
        if pat is not None:
            pat = {**pat, "is_new": not self.existing}
        # hide the patient identity block until documents have actually been read
        show_patient = self.state in ("review", "done") or self.state == "mismatch"
        return {
            "job_id": self.id,
            "state": self.state,
            "patient_id": self.patient_id if show_patient else None,
            "existing_patient": self.existing,
            "patient": pat if show_patient else None,
            "mismatch": self.mismatch,
            "error": self.error,
            "stages": STAGES,
            "documents": [
                {"filename": d.filename, "document_id": d.document_id, "doc_type": d.doc_type,
                 "status": d.status, "stages": d.stages, "facts": d.facts,
                 "accepted": d.accepted, "in_review": d.in_review,
                 "seconds": round(d.seconds, 1) if d.seconds else None,
                 "error": d.error}
                for d in self.docs
            ],
            "artifact_count": (self.result or {}).get("artifact_count"),
            "ready_to_share": (self.result or {}).get("ready_to_share"),
            "needs_review": (self.result or {}).get("needs_review"),
            "review_open": (self.result or {}).get("review_open"),
            "has_result": self.result is not None,
        }


def create_job(abha: str | None, files: list[tuple[str, bytes]],
               patient_ref: str | None = None) -> str:
    jid = uuid.uuid4().hex[:12]
    job = Job(id=jid, abha=(abha or "").strip() or None)
    job.docs = [DocProg(filename=fn) for fn, _ in files]

    ref = (patient_ref or "").strip()
    if ref:
        from ..mpi import registry
        with session_scope() as sess:
            reg = registry.lookup(sess, ref)
            if reg:
                pid, mpi = registry.ensure_identity(sess, reg)
                job.patient_id = pid
                job.existing = True
                job.patient = {
                    "mpi_id": mpi, "name": reg.get("name"),
                    "sex": (reg.get("gender") or "")[:1].upper() or None,
                    "birth_date": reg.get("dob"), "mobile": reg.get("mobile"),
                    "address": reg.get("address"), "abha_number": reg.get("abha_id"),
                    "identity_confidence": 1.0, "provisional": False}
                if not job.abha and reg.get("abha_id"):
                    job.abha = reg["abha_id"]

    with _lock:
        _jobs[jid] = job
    _pool.submit(_run_job, jid, files)
    return jid


def get_job(jid: str) -> Job | None:
    return _jobs.get(jid)


# --------------------------------------------------------------------------- #
def _stage1(prog: DocProg, fn: str, raw: bytes, abha: str | None) -> None:
    """ingest -> classify (fast) -> OCR.  CPU-bound; runs in parallel per document."""
    try:
        prog.status = "running"
        prog.stage("ingest", "running")
        res = ingest_bytes(raw, filename=fn, source_channel="webapp", legacy_patient_ref=abha)
        prog.document_id = res.document_id
        prog.stage("ingest", "done")

        prog.stage("classify", "running")
        c = classify_document(res.document_id)
        prog.doc_type = c.doc_type
        prog.stage("classify", "done")

        prog.stage("ocr", "running")
        ocr_document(res.document_id)
        prog.stage("ocr", "done")
    except Exception as exc:  # noqa: BLE001
        prog.status = "error"
        prog.error = str(exc)[:400]
        for s in STAGES:
            if prog.stages[s] == "running":
                prog.stage(s, "error")
        raise


def _stage2(job: "Job", prog: DocProg,
            candidates: list[IdentityCandidate]) -> None:
    """extract (GPU) -> terminology -> validate.  Serialised across documents."""
    if prog.status == "error" or not prog.document_id:
        return
    try:
        prog.stage("extract", "running")
        ex = extract_document(prog.document_id, patient_id=job.patient_id, abha_hint=job.abha)
        prog.facts = ex.n_facts
        if ex.patient_id and not job.patient_id:
            job.patient_id = ex.patient_id

        # existing patient selected -> the document must belong to that person
        if job.existing and ex.identity and (ex.identity.get("name") or "").strip():
            reg = job.patient or {}
            doc_name = ex.identity["name"]
            doc_dob = parse_dob(ex.identity.get("birth_date"))
            reg_dob = parse_dob(reg.get("birth_date"))
            if not names_match(doc_name, reg.get("name")) or not dob_match(doc_dob, reg_dob):
                job.mismatch = {
                    "selected_name": reg.get("name"), "selected_id": reg.get("mpi_id"),
                    "selected_dob": reg.get("birth_date"),
                    "document": prog.filename,
                    "document_name": doc_name,
                    "document_dob": ex.identity.get("birth_date"),
                }
                job.state = "mismatch"
                prog.status = "error"
                prog.error = f"document is for {doc_name}, not {reg.get('name')}"
                prog.stage("extract", "error")
                # undo what this document just wrote against the wrong patient
                try:
                    with session_scope() as s:
                        repo.purge_document_facts(s, prog.document_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("mismatch_purge_failed", error=str(exc)[:150])
                log.warning("patient_mismatch", job=job.id, selected=reg.get("name"),
                            found=doc_name)
                return

        if ex.identity:
            i = ex.identity
            _age_y, bd_age = parse_age(i.get("age_years"))
            candidates.append(IdentityCandidate(
                name_full=i.get("name"), sex=i.get("sex"), age_years=i.get("age_years"),
                birth_date=parse_dob(i.get("birth_date")) or bd_age,
                abha=job.abha, source_doc_id=prog.document_id))
            job.patient = {"mpi_id": ex.mpi_id, "name": i.get("name"), "sex": i.get("sex"),
                           "birth_date": i.get("birth_date"), "age_years": i.get("age_years"),
                           "abha_number": job.abha, "provisional": True}
        prog.stage("extract", "done")

        prog.stage("terminology", "running")
        bind_document(prog.document_id)
        prog.stage("terminology", "done")

        prog.stage("validate", "running")
        v = validate_document(prog.document_id)
        prog.accepted, prog.in_review = v.auto_accepted, v.in_review
        prog.stage("validate", "done")

        prog.status = "done"
        prog.seconds = time.time() - prog.t0
    except Exception as exc:  # noqa: BLE001
        prog.status = "error"
        prog.error = str(exc)[:400]
        for s in STAGES:
            if prog.stages[s] == "running":
                prog.stage(s, "error")
        log.error("job_doc_failed", job=job.id, file=prog.filename,
                  error=str(exc)[:300], tb=traceback.format_exc()[-700:])


def _run_job(jid: str, files: list[tuple[str, bytes]]) -> None:
    job = _jobs[jid]
    job.state = "running"
    candidates: list[IdentityCandidate] = []
    try:
        # --- phase 1: ingest + classify + OCR for every document, concurrently ---
        futs = [_pool.submit(_stage1, prog, fn, raw, job.abha)
                for prog, (fn, raw) in zip(job.docs, files)]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception:  # noqa: BLE001  (already recorded on the DocProg)
                pass

        # --- phase 2: extract + terminology + validate, serial on the single GPU ---
        for prog in job.docs:
            _stage2(job, prog, candidates)
            if job.state == "mismatch":
                break

        if job.state == "mismatch":
            job.error = (f"Uploaded document is for {job.mismatch['document_name']}"
                         f" but you selected {job.mismatch['selected_name']}"
                         f" ({job.mismatch['selected_id']}). Processing stopped -"
                         f" nothing was written for the wrong patient.")
            log.warning("job_stopped_mismatch", job=jid)
            return

        # --- phase 3: finalise identity (skip for an existing registry patient) ---
        if job.patient_id and candidates and not job.existing:
            with session_scope() as sess:
                job.patient = merge_identity_evidence(sess, job.patient_id, candidates)

        job.state = "review" if any(d.status == "done" for d in job.docs) else "error"
        _refresh_result(job)
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.error = str(exc)[:500]
        log.error("job_failed", job=jid, error=str(exc)[:400], tb=traceback.format_exc()[-1000:])


def _refresh_result(job: "Job") -> None:
    if not job.patient_id:
        job.result = {"patient": None, "bundles": [], "artifact_count": 0,
                      "ready_to_share": 0, "needs_review": 0}
        return
    job.result = project_patient(job.patient_id)
    with session_scope() as sess:
        job.result["review_open"] = len(repo.list_review_tasks(sess, patient_id=job.patient_id))


# --------------------------------------------------------------------------- #
def _num(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _med(md: dict[str, Any]) -> dict[str, Any]:
    return {"dose": _num(md.get("dose_num") or md.get("strength_num")),
            "unit": md.get("dose_unit_ucum") or md.get("strength_unit"),
            "freq": md.get("frequency_code"),
            "freq_per_day": _num(md.get("frequency_per_day")),
            "route": md.get("route")}


def job_facts(jid: str) -> dict[str, Any]:
    """All extracted facts for a job, grouped by document, for the inline editor."""
    job = _jobs.get(jid)
    if not job or not job.patient_id:
        raise KeyError(jid)
    out_docs = []
    with session_scope() as sess:
        prow = sess.execute(
            __import__("sqlalchemy").text("SELECT * FROM patient_identity WHERE id=:i"),
            {"i": job.patient_id}).mappings().first()
        for prog in job.docs:
            if not prog.document_id:
                continue
            facts = repo.list_clinical_facts(sess, document_id=prog.document_id)
            pages = repo.list_document_pages(sess, prog.document_id)
            img = (f"api/documents/{prog.document_id}/pages/{pages[0]['page_no']}"
                   if pages else None)
            rows = []
            for f in facts:
                if f["review_state"] in ("rejected",) or not f["is_current"]:
                    continue
                prov = repo.get_fact_provenance(sess, f["id"])
                bbox = next((list(p["bbox_union"]) for p in prov if p.get("bbox_union")), None)
                md = (repo.get_medication_detail(sess, f["id"])
                      if f["fact_type"] == "medication" else None)
                mv = _med(md) if md else None
                row = {
                    "fact_id": str(f["id"]), "fact_type": f["fact_type"],
                    "text": f["local_text"],
                    "value_num": float(f["value_num"]) if f["value_num"] is not None else None,
                    "value_unit_ucum": f["value_unit_ucum"], "value_text": f["value_text"],
                    "freq_text": None,
                    "code_system": f["code_system"], "code": f["code"],
                    "code_display": f["code_display"], "code_status": f["code_status"],
                    "abnormal_flag": f["abnormal_flag"],
                    "confidence": float(f["confidence_overall"] or 0),
                    "review_state": f["review_state"],
                    "medication": mv,
                    "bbox": bbox, "page_width": pages[0]["width_px"] if pages else None,
                    "page_height": pages[0]["height_px"] if pages else None,
                }
                if mv:  # surface dose / frequency into the editable columns
                    row["value_num"] = mv.get("dose")
                    row["value_unit_ucum"] = mv.get("unit")
                    row["freq_text"] = mv.get("freq") or (
                        f"{mv['freq_per_day']:g}/day" if mv.get("freq_per_day") else None)
                rows.append(row)
            out_docs.append({"document_id": prog.document_id, "filename": prog.filename,
                             "doc_type": prog.doc_type, "page_image_url": img,
                             "seconds": round(prog.seconds, 1) if prog.seconds else None,
                             "facts": rows})
    patient = {
        "mpi_id": prow["mpi_id"] if prow else None,
        "name": (prow.get("name_full") if prow else None),
        "sex": prow.get("gender") if prow else None,
        "birth_date": str(prow["birth_date"])[:10] if prow and prow.get("birth_date") else None,
        "age_years": prow.get("age_years") if prow else None,
        "abha_number": prow.get("abha_number") if prow else None,
        "identity_confidence": (float(prow["identity_confidence"])
                                if prow and prow.get("identity_confidence") is not None else None),
        "is_new": not job.existing,
    }
    if prow and prow.get("mpi_id"):
        with session_scope() as sess2:
            reg = sess2.execute(
                __import__("sqlalchemy").text(
                    "SELECT mobile, address FROM patient_registry WHERE patient_id=:m LIMIT 1"),
                {"m": prow["mpi_id"]}).mappings().first()
        if reg:
            patient["mobile"] = reg["mobile"]
            patient["address"] = reg["address"]
    return {"job_id": jid, "state": job.state, "patient": patient, "documents": out_docs}


def apply_edits_and_generate(jid: str, edits: list[dict[str, Any]],
                             reviewer: str = "reviewer") -> dict[str, Any]:
    """Bulk-apply the editor's decisions, then re-project the FHIR bundles (ms)."""
    from . import review as review_svc

    job = _jobs.get(jid)
    if not job or not job.patient_id:
        raise KeyError(jid)
    job.state = "generating"
    t0 = time.time()
    applied = {"keep": 0, "correct": 0, "drop": 0}
    for e in edits:
        fid = e.get("fact_id")
        if not fid:
            continue
        action = (e.get("action") or "keep").lower()
        try:
            if action == "drop":
                review_svc.submit_decision(fid, "reject", reviewer=reviewer)
                applied["drop"] += 1
            elif e.get("corrections"):
                review_svc.submit_decision(fid, "correct", reviewer=reviewer,
                                           corrections=e["corrections"])
                applied["correct"] += 1
            else:
                review_svc.submit_decision(fid, "accept", reviewer=reviewer)
                applied["keep"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("edit_apply_failed", fact=fid, error=str(exc)[:150])
    _refresh_result(job)
    job.state = "done"
    ms = int((time.time() - t0) * 1000)
    log.info("bundles_generated", job=jid, applied=applied, ms=ms)
    return {**job.result, "applied": applied, "generate_ms": ms}
