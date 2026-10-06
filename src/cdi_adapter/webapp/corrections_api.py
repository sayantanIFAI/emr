"""HTTP side of the correction loop.

``POST /api/corrections`` is what the human-correction tool calls::

    {"prescription_id": "<document id>", "field_id": "<field id from field-records>",
     "corrected_value": "Serum Creatinine", "reviewer_id": "dr.rao"}

The correction is applied to the field (through the same review path as the reviewer console, so the
immutable ledger and audit trail see it), then stored with the original prediction and both engines'
readings (append-only), and the doctor's lexicon is updated. Reads: the doctor's profile, the records
for a document's fields, and the training export. Every refusal is one plain sentence.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..corrections import cache as profile_cache
from ..corrections import service as svc
from ..db import session_scope
from ..logging import get_logger
from . import review

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["corrections"])


class CorrectionIn(BaseModel):
    prescription_id: str = Field(max_length=64)
    field_id: str = Field(max_length=64)
    corrected_value: str = Field(max_length=2000)
    reviewer_id: str = Field(max_length=100)


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, f"That {what} was not found.") from None


@router.post("/corrections", status_code=201)
def post_correction(body: CorrectionIn) -> dict[str, Any]:
    doc, fact = _uuid(body.prescription_id, "prescription"), _uuid(body.field_id, "field")
    try:
        reviewer = svc.clean_reviewer(body.reviewer_id)
        with session_scope() as sess:
            ctx = svc.load_context(sess, doc, fact)
        value = svc.clean_value(body.corrected_value, ctx.original_value)      # refuse before anything changes
    except svc.CorrectionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    try:
        review.submit_decision(fact, "correct", reviewer=reviewer, corrections={"local_text": value},
                               note="correction via the corrections API")
    except ValueError as exc:
        raise HTTPException(404, "That field was not found.") from exc
    try:
        with session_scope() as sess:
            done = svc.record_correction(sess, ctx, value, reviewer)
    except Exception as exc:  # noqa: BLE001 - the correction is applied; say plainly that the record failed
        log.error("correction_record_failed", document_id=doc, fact_id=fact, error=str(exc)[:200])
        return {"ok": True, "applied": True, "recorded": False,
                "note": "The correction was applied but could not be recorded for learning."}
    if done["doctor_id"]:
        cache = profile_cache.get_cache()
        if cache is not None:
            cache.invalidate(done["doctor_id"])
    return {"ok": True, "applied": True, "recorded": True, **done}


@router.get("/documents/{document_id}/field-records")
def get_field_records(document_id: str) -> list[dict[str, Any]]:
    """One record per extracted field: ids, doctor, both engines' readings, final value, confidence, status."""
    doc = _uuid(document_id, "prescription")
    with session_scope() as sess:
        return svc.field_records(sess, doc)


@router.get("/doctors/{doctor_id}/profile")
def get_doctor_profile(doctor_id: str, field_type: str | None = None) -> dict[str, Any]:
    doc = _uuid(doctor_id, "doctor")
    with session_scope() as sess:
        return svc.get_profile(sess, doc, field_type, cache=profile_cache.get_cache())


@router.get("/corrections/export")
def export_corrections(since: str | None = None, limit: int | None = None) -> Response:
    """The training data: one JSON object per line, oldest first."""
    when = None
    if since:
        try:
            when = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(422, "'since' must be a date or date-time like 2026-10-01 or 2026-10-01T09:00:00Z.") from None
    if limit is not None and not 1 <= limit <= 100_000:
        raise HTTPException(422, "'limit' must be between 1 and 100000.")
    with session_scope() as sess:
        rows = svc.export_training(sess, when, limit)
    body = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
    return Response(content=body, media_type="application/x-ndjson")
