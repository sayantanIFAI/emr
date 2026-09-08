from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .. import repo
from ..classify.service import classify_document
from ..db import session_scope
from ..extract.service import extract_document
from ..fhir.service import project_patient
from ..ingest.service import ingest_bytes
from ..logging import get_logger
from ..mpi.service import IdentityCandidate, merge_identity_evidence
from ..ocr.service import ocr_document
from ..terminology.service import bind_document
from ..validate.service import validate_document

log = get_logger(__name__)

# one worker: the VLM gateway is single-GPU, serialise pipeline runs
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cdi-job")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()

_STEPS = ["ingest", "classify", "ocr", "extract", "terminology", "validate"]


@dataclass
class DocProg:
    filename: str
    document_id: str | None = None
    doc_type: str | None = None
    status: str = "queued"          # queued|running|done|error
    step: str | None = None
    facts: int = 0
    accepted: int = 0
    in_review: int = 0
    error: str | None = None


@dataclass
class Job:
    id: str
    abha: str | None
    patient_id: str | None = None
    patient: dict[str, Any] | None = None      # {mpi_id, name, sex, birth_date, ...}
    state: str = "queued"          # queued|running|done|error
    created: float = field(default_factory=time.time)
    docs: list[DocProg] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "state": self.state,
            "patient_id": self.patient_id,
            "patient": self.patient or (self.result or {}).get("patient"),
            "error": self.error,
            "documents": [
                {"filename": d.filename, "document_id": d.document_id, "doc_type": d.doc_type,
                 "status": d.status, "step": d.step, "facts": d.facts,
                 "accepted": d.accepted, "in_review": d.in_review, "error": d.error}
                for d in self.docs
            ],
            "artifact_count": (self.result or {}).get("artifact_count"),
            "ready_to_share": (self.result or {}).get("ready_to_share"),
            "needs_review": (self.result or {}).get("needs_review"),
            "review_open": (self.result or {}).get("review_open"),
            "has_result": self.result is not None,
        }


def create_job(abha: str | None, files: list[tuple[str, bytes]]) -> str:
    jid = uuid.uuid4().hex[:12]
    job = Job(id=jid, abha=(abha or "").strip() or None)
    job.docs = [DocProg(filename=fn) for fn, _ in files]
    with _lock:
        _jobs[jid] = job
    _pool.submit(_run_job, jid, files)
    return jid


def get_job(jid: str) -> Job | None:
    return _jobs.get(jid)


def _run_job(jid: str, files: list[tuple[str, bytes]]) -> None:
    job = _jobs[jid]
    job.state = "running"
    candidates: list[IdentityCandidate] = []
    try:
        for prog, (fn, raw) in zip(job.docs, files):
            prog.status = "running"
            try:
                prog.step = "ingest"
                res = ingest_bytes(raw, filename=fn, source_channel="webapp",
                                   legacy_patient_ref=job.abha)
                prog.document_id = res.document_id

                prog.step = "classify"
                c = classify_document(res.document_id)
                prog.doc_type = c.doc_type

                prog.step = "ocr"
                ocr_document(res.document_id)

                prog.step = "extract"
                # first doc: no patient yet -> MPI resolves/creates it from the doc
                ex = extract_document(
                    res.document_id, patient_id=job.patient_id, abha_hint=job.abha,
                )
                prog.facts = ex.n_facts
                if ex.patient_id and not job.patient_id:
                    job.patient_id = ex.patient_id
                if ex.identity:
                    i = ex.identity
                    from ..mpi.service import parse_age, parse_dob
                    age_y, bd_age = parse_age(i.get("age_years"))
                    bd = parse_dob(i.get("birth_date")) or bd_age
                    candidates.append(IdentityCandidate(
                        name_full=i.get("name"), sex=i.get("sex"),
                        age_years=i.get("age_years"), birth_date=bd,
                        abha=job.abha, source_doc_id=res.document_id,
                    ))
                    job.patient = {"mpi_id": ex.mpi_id, "name": i.get("name"),
                                   "sex": i.get("sex"), "birth_date": i.get("birth_date"),
                                   "age_years": i.get("age_years"), "abha_number": job.abha,
                                   "provisional": True}

                prog.step = "terminology"
                bind_document(res.document_id)

                prog.step = "validate"
                v = validate_document(res.document_id)
                prog.accepted = v.auto_accepted
                prog.in_review = v.in_review

                prog.step = None
                prog.status = "done"
            except Exception as exc:  # noqa: BLE001
                prog.status = "error"
                prog.error = str(exc)[:400]
                log.error("job_doc_failed", job=jid, file=fn, error=str(exc)[:300],
                          tb=traceback.format_exc()[-800:])

        if job.patient_id and candidates:
            with session_scope() as sess:
                job.patient = merge_identity_evidence(sess, job.patient_id, candidates)

        if job.patient_id:
            job.result = project_patient(job.patient_id)
            with session_scope() as sess:
                job.result["review_open"] = len(
                    repo.list_review_tasks(sess, patient_id=job.patient_id))
        else:
            job.result = {"patient": None, "bundles": [], "artifact_count": 0,
                          "ready_to_share": 0, "needs_review": 0}
        job.state = "done" if any(d.status == "done" for d in job.docs) else "error"
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.error = str(exc)[:500]
        log.error("job_failed", job=jid, error=str(exc)[:400], tb=traceback.format_exc()[-1000:])
