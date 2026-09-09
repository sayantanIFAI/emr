from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from ..ml.client import MLError, _stub_classify, get_client
from .prompt import CLASSIFICATION_SCHEMA, build_classification_prompt

log = get_logger(__name__)


@dataclass
class ClassifyResult:
    document_id: str
    doc_type: str
    is_handwritten: bool
    languages: list[str]
    confidence: float
    skipped: bool = False


def classify_document(document_id: str) -> ClassifyResult:
    client = get_client()
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise ValueError(f"document {document_id} not found")
        pages = repo.list_document_pages(sess, document_id)
        if not pages:
            raise ValueError(f"document {document_id} has no rendered pages")
        run_id = repo.start_pipeline_run(
            sess, document_id=document_id, stage="classify",
            model_name="mlserve/vlm", params={"schema": "classification.v1"},
        )

    hint = _ocr_hint(pages[0])
    prompt = build_classification_prompt(n_pages=len(pages), page_hint=hint)
    used_model = "mlserve/vlm"

    obj: dict[str, Any] | None = None
    # fast path: keyword classify on the OCR text, skip the VLM when unambiguous
    if settings.fast_classify and hint:
        kw = _stub_classify(prompt)
        if kw.get("doc_type") and kw["doc_type"] != "other":
            obj = kw
            used_model = "fast/keyword"

    if obj is None:
        image = storage.get_bytes(storage.key_from_uri(pages[0]["image_uri"]))
        try:
            obj = client.vlm_json(image, prompt, CLASSIFICATION_SCHEMA, max_tokens=500)
        except MLError as exc:
            with session_scope() as sess:
                repo.finish_pipeline_run(sess, run_id, status="failed", error_detail=str(exc)[:400])
                repo.set_document_status(sess, document_id, "error", error_detail=f"classify: {exc}")
            raise

    doc_type = obj["doc_type"]
    is_hw = bool(obj["is_handwritten"])
    langs = obj.get("languages") or ["en"]
    conf = float(obj["confidence"])

    with session_scope() as sess:
        repo.insert_doc_classification(
            sess,
            document_id=document_id,
            doc_type=doc_type,
            specialty=obj.get("specialty"),
            is_handwritten=is_hw,
            languages=langs,
            page_spans=obj.get("page_spans") or [],
            confidence=conf,
            model_run_id=run_id,
        )
        repo.finish_pipeline_run(
            sess, run_id, status="ok",
            metrics={"doc_type": doc_type, "handwritten": is_hw, "confidence": conf,
                     "via": used_model},
        )
        repo.set_document_status(sess, document_id, "classified")
        repo.write_audit(
            sess, actor="classify-svc", action="create", entity="doc_classification",
            entity_id=document_id,
            detail={"doc_type": doc_type, "handwritten": is_hw, "languages": langs},
        )

    log.info(
        "classified", document_id=document_id, doc_type=doc_type,
        handwritten=is_hw, confidence=conf,
    )
    _enqueue_ocr(document_id)
    return ClassifyResult(document_id, doc_type, is_hw, langs, conf)


def _ocr_hint(page: dict) -> str | None:
    """Fast, low-res OCR of the thumbnail to give the classifier a text anchor.

    Best-effort: if the OCR engine isn't installed or fails, the VLM still
    classifies from the image alone.
    """
    try:
        from ..ocr.rapid import run_rapidocr

        key = storage.key_from_uri(page["image_uri"])
        lines = run_rapidocr(storage.get_bytes(key))
        text = "\n".join(ln.text for ln in lines[:25])
        return text or None
    except Exception as exc:  # noqa: BLE001
        log.debug("ocr_hint_skipped", error=str(exc)[:150])
        return None


def _enqueue_ocr(document_id: str) -> None:
    try:
        from ..worker import ocr_document

        ocr_document.delay(document_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("enqueue_ocr_skipped", document_id=document_id, error=str(exc))
