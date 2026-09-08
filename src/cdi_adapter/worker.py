"""Celery worker: durable hand-off between pipeline stages.

Chain (by document_id):
    ingest_file -> classify_document (S2) -> ocr_document (S3) -> extract_document (S4, stub)
Each stage is idempotent and records its own ``pipeline_run`` row.
"""
from __future__ import annotations

from pathlib import Path

from celery import Celery

from .config import settings
from .db import session_scope
from .logging import get_logger
from . import repo

log = get_logger(__name__)

celery_app = Celery("cdi_adapter", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="cdi",
    worker_prefetch_multiplier=1,
    result_expires=3600,
)


@celery_app.task(name="cdi.ingest_file", bind=True, max_retries=3, default_retry_delay=10)
def ingest_file(self, path: str, meta: dict | None = None):  # noqa: ANN001
    from .ingest.service import ingest_bytes

    p = Path(path)
    meta = meta or {}
    try:
        raw = p.read_bytes()
        res = ingest_bytes(
            raw,
            filename=p.name,
            source_channel=meta.get("source_channel", "batch"),
            legacy_ref=meta.get("legacy_ref"),
            legacy_patient_ref=meta.get("legacy_patient_ref"),
        )
        return {"document_id": res.document_id, "dedup": res.deduplicated, "pages": res.page_count}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.classify_document", bind=True, max_retries=3, default_retry_delay=15)
def classify_document(self, document_id: str):  # noqa: ANN001
    from .classify.service import classify_document as _run

    try:
        r = _run(document_id)
        return {
            "document_id": r.document_id, "doc_type": r.doc_type,
            "handwritten": r.is_handwritten, "confidence": r.confidence,
        }
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.ocr_document", bind=True, max_retries=3, default_retry_delay=20)
def ocr_document(self, document_id: str):  # noqa: ANN001
    from .ocr.service import ocr_document as _run

    try:
        r = _run(document_id)
        return {"document_id": r.document_id, "engine": r.engine, "blocks": r.n_blocks}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.extract_document", bind=True, max_retries=2, default_retry_delay=20)
def extract_document(self, document_id: str):  # noqa: ANN001
    from .extract.service import extract_document as _run

    try:
        r = _run(document_id)
        bind_document.delay(document_id)
        return {"document_id": document_id, "facts": r.n_facts, "schema": r.schema}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.bind_document", bind=True, max_retries=2, default_retry_delay=10)
def bind_document(self, document_id: str):  # noqa: ANN001
    from .terminology.service import bind_document as _run

    try:
        counts = _run(document_id)
        validate_document.delay(document_id)
        return {"document_id": document_id, **counts}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.validate_document", bind=True, max_retries=2, default_retry_delay=10)
def validate_document(self, document_id: str):  # noqa: ANN001
    from .validate.service import validate_document as _run

    try:
        r = _run(document_id)
        project_document.delay(document_id)
        return {"document_id": document_id, "auto_accepted": r.auto_accepted,
                "in_review": r.in_review, "conflicts": r.conflicts}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(name="cdi.project_document", bind=True, max_retries=2, default_retry_delay=10)
def project_document(self, document_id: str):  # noqa: ANN001
    from .fhir.service import project_document as _run

    try:
        r = _run(document_id)
        return {"document_id": document_id, "artifact": r["artifact_type"],
                "resources": r["resource_count"], "issues": len(r["issues"])}
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)
