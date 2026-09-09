from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from ..ml.client import MLError, get_client
from .prompt import CLASSIFICATION_SCHEMA, build_classification_prompt

log = get_logger(__name__)

# (doc_type, weight, pattern) - scored keyword signals over the page's OCR text.
# Weights: 3 = a near-unique anchor, 2 = strong, 1 = corroborating.
_SIGNALS: list[tuple[str, int, "re.Pattern[str]"]] = [
    # ---- prescription ----
    ("prescription", 3, re.compile(r"\br[x×]\b|℞|\bprescription\b")),
    ("prescription", 2, re.compile(r"\b\d\s*-\s*\d\s*-\s*\d\b")),                 # 1-0-1 dosing
    ("prescription", 2, re.compile(r"\b(tab|cap|syp|inj|susp|oint)\.?\s+[a-z]")),
    ("prescription", 2, re.compile(r"\b(bd|od|tds|qid|qds|hs|sos|stat|prn)\b")),
    ("prescription", 1, re.compile(r"\b(after|before)\s+food\b|\bempty stomach\b")),
    ("prescription", 1, re.compile(r"\b(once|twice|thrice)\s+(a\s+)?day\b|\bdaily\b")),
    ("prescription", 1, re.compile(r"\bsig\b|\bdispense\b|\brefill\b|\bfollow\s*up\b")),
    # ---- lab_report ----
    ("lab_report", 3, re.compile(r"\breference\s+(range|interval|value)\b|\bref\.?\s*range\b|\bbio\.?\s*ref\b")),
    ("lab_report", 3, re.compile(r"\b(biochemistry|haematology|hematology|serology|microbiology|histopathology)\b[\s\w]*\breport\b|\blaboratory\s+report\b|\blab\.?\s*report\b")),
    ("lab_report", 2, re.compile(r"\b(hba1c|creatinine|haemoglobin|hemoglobin|bilirubin|cholesterol|triglyceride|tsh|wbc|rbc|esr|crp|urea|electrolyte)\b")),
    ("lab_report", 2, re.compile(r"\b(specimen|sample)\b[\s\w]*\b(collected|drawn|received)\b|\bcollected\s+on\b|\breported\s+on\b")),
    ("lab_report", 1, re.compile(r"\bmg\s*/\s*dl\b|\bmmol\s*/\s*l\b|\bg\s*/\s*dl\b|\biu\s*/\s*l\b|\bng\s*/\s*ml\b|\bmeq\s*/\s*l\b|\bcells?\s*/\s*cumm\b")),
    ("lab_report", 1, re.compile(r"\bresult\b[\s\w]*\b(unit|flag|range)\b")),
    # ---- radiology_report ----
    ("radiology_report", 3, re.compile(r"\bimpression\s*[:\-]")),
    ("radiology_report", 3, re.compile(r"\b(x[\s\-]?ray|radiograph|ultrasonograph|ultrasound|usg|ct\s+(scan|abdomen|chest|brain|head|thorax)|mri\b|echocardiograph|\becho\b|doppler|mammograph|angiograph|angiogram|pet\s+scan|fluoroscop|hrct|\bkub\b)\b")),
    ("radiology_report", 2, re.compile(r"\bfindings?\s*[:\-]")),
    ("radiology_report", 2, re.compile(r"\btechnique\s*[:\-]|\bpost[\s\-]?contrast\b|\bcontrast\b[\s\w]*\b(administered|injected|study)\b")),
    ("radiology_report", 1, re.compile(r"\bno\s+(significant|acute)\s+(abnormalit|finding)|\bunremarkable\b|\bwithin normal limits\b")),
    ("radiology_report", 1, re.compile(r"\b(coronal|sagittal|axial)\b|\b(ap|pa)\s+(and\s+)?lateral\s+view")),
    # ---- discharge_summary ----
    ("discharge_summary", 4, re.compile(r"\bdischarge\s+summary\b")),
    ("discharge_summary", 2, re.compile(r"\bdate\s+of\s+(admission|discharge)\b|\badmitted\s+on\b|\bdischarged\s+on\b")),
    ("discharge_summary", 2, re.compile(r"\bhospital\s+course\b|\bcondition\s+(at|on)\s+discharge\b|\bcourse\s+in\s+(the\s+)?hospital\b")),
    ("discharge_summary", 2, re.compile(r"\b(discharge|d/c)\s+medication|\btreatment\s+on\s+discharge\b")),
    # ---- vitals_sheet ----
    ("vitals_sheet", 3, re.compile(r"\b(nursing|vital)\s+(chart|sheet|record|flow)|\bintake[\s/\-]*output\b|\bi\s*/\s*o\s+chart\b|\btpr\s+chart\b")),
    ("vitals_sheet", 2, re.compile(r"\bspo2\b\s*[:=]?\s*\d|\bo2\s+sat")),
    ("vitals_sheet", 1, re.compile(r"\bbp\b\s*[:=]?\s*\d{2,3}\s*/\s*\d{2,3}")),
    ("vitals_sheet", 1, re.compile(r"\bpulse\b\s*[:=]?\s*\d|\btemp(erature)?\b\s*[:=]?\s*\d|\bresp(iratory)?\s+rate\b|\bgcs\b\s*[:=]?\s*\d")),
    # ---- opd_note / referral ----
    ("opd_note", 3, re.compile(r"\bopd\s+(note|record|slip|card)\b|\bprogress\s+note\b|\bconsultation\s+note\b|\bout[\s\-]?patient\s+record\b")),
    ("opd_note", 2, re.compile(r"\bchief\s+complaint|\bc/o\b|\bpresenting\s+complaint\b|\bhistory\s+of\s+present")),
    ("opd_note", 2, re.compile(r"\bon\s+examination\b|\bo/e\b|\bprovisional\s+diagnosis\b|\bdifferential\s+diagnosis\b")),
    # ---- operative_note ----
    ("operative_note", 4, re.compile(r"\boperative?\s+note\b|\boperation\s+(note|record)\b|\bsurgical\s+note\b")),
    ("operative_note", 2, re.compile(r"\b(pre|post)[\s\-]?operative\s+diagnosis\b|\bprocedure\s+performed\b|\bname\s+of\s+(the\s+)?(operation|procedure)\b")),
    ("operative_note", 2, re.compile(r"\bsurgeon\b\s*[:=]|\banaesthesi|\banesthesi|\bestimated\s+blood\s+loss\b|\bebl\b\s*[:=]")),
]


def _heuristic_classify(text: str | None) -> dict[str, Any] | None:
    """Scored keyword classification over the page OCR text.

    Returns a classification only when the winning doc type is both strong and
    clearly ahead of the runner-up on a cleanly OCR'd page; every other case
    returns None so the VLM still decides. Deliberately conservative - it trades
    coverage for never handing the extractor the wrong schema.
    """
    if not text:
        return None
    t = text.lower()
    if len(t) < 180:                                     # too little to be sure
        return None
    legible = sum(c.isalnum() or c.isspace() for c in t) / len(t)
    if legible < 0.75:                                   # noisy / likely handwriting
        return None

    scores: dict[str, int] = {}
    for dt, w, rx in _SIGNALS:
        if rx.search(t):
            scores[dt] = scores.get(dt, 0) + w
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_dt, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best < 5 or (best - second) < 3:                  # weak or ambiguous
        return None

    return {
        "doc_type": best_dt,
        "specialty": None,
        "is_handwritten": False,                         # only reached on clean OCR
        "languages": ["en"],
        "page_spans": [],
        "confidence": round(min(0.72 + 0.04 * best, 0.92), 3),
        "rationale": f"heuristic score {best} for {best_dt}, margin {best - second}",
    }


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
    # fast path: scored heuristic on the OCR text; only wins on an unambiguous,
    # cleanly OCR'd page - otherwise fall through to the VLM
    if settings.fast_classify and hint:
        h = _heuristic_classify(hint)
        if h is not None:
            obj = h
            used_model = "fast/heuristic"

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
        text = "\n".join(ln.text for ln in lines[:60])
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
