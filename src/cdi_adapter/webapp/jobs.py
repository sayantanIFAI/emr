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
    patient_name: str
    abha: str | None
    gender: str | None
    patient_id: str | None = None
    state: str = "queued"          # queued|running|done|error
    created: float = field(default_factory=time.time)
    docs: list[DocProg] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "state": self.state,
            "patient_name": self.patient_name,
            "patient_id": self.patient_id,
            "error": self.error,
            "documents": [
                {"filename": d.filename, "document_id": d.document_id, "doc_type": d.doc_type,
                 "status": d.status, "step": d.step, "facts": d.facts,
                 "accepted": d.accepted, "in_review": d.in_review, "error": d.error}
                for d in self.docs
            ],
            "artifact_count": (self.result or {}).get("artifact_count"),
            "review_open": (self.result or {}).get("review_open"),
            "has_result": self.result is not None,
        }


def create_job(patient_name: str, abha: str | None, gender: str | None,
               files: list[tuple[str, bytes]]) -> str:
    jid = uuid.uuid4().hex[:12]
    job = Job(id=jid, patient_name=patient_name.strip() or "Unknown",
              abha=(abha or "").strip() or None, gender=(gender or "").strip() or None)
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
    try:
        given, _, family = job.patient_name.partition(" ")
        with session_scope() as sess:
            pid = str(repo.get_or_create_patient(
                sess, name_given=given or job.patient_name, name_family=family or None,
                abha_number=job.abha, gender=job.gender,
            ))
        job.patient_id = pid

        for prog, (fn, raw) in zip(job.docs, files):
            prog.status = "running"
            try:
                prog.step = "ingest"
                res = ingest_bytes(raw, filename=fn, source_channel="webapp",
                                   legacy_patient_ref=job.abha or job.patient_name)
                prog.document_id = res.document_id

                prog.step = "classify"
                c = classify_document(res.document_id)
                prog.doc_type = c.doc_type

                prog.step = "ocr"
                ocr_document(res.document_id)

                prog.step = "extract"
                ex = extract_document(res.document_id, patient_id=pid)
                prog.facts = ex.n_facts

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

        job.result = project_patient(pid)
        with session_scope() as sess:
            job.result["review_open"] = len(repo.list_review_tasks(sess, patient_id=pid))
        job.state = "done" if any(d.status == "done" for d in job.docs) else "error"
    except Exception as exc:  # noqa: BLE001
        job.state = "error"
        job.error = str(exc)[:500]
        log.error("job_failed", job=jid, error=str(exc)[:400], tb=traceback.format_exc()[-1000:])
