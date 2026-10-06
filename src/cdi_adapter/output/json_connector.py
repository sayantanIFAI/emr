"""JSON placeholder connector (UP-S3, for now).

A *connector* turns one finished document into whatever a downstream system wants. The real
downstream (HIS / EMR) is not chosen yet, so this placeholder emits a plain, versioned JSON that
the upload screen shows and lets the person download. When the real contract (OUT-S2,
``result.v1``) exists it replaces :class:`JsonPlaceholderConnector` behind the same interface;
nothing else changes.

Rules the JSON keeps (the point of a placeholder is that these hold from day one):

* every value is an object ``{"value", "status", "reason", "confidence"}``: a value is never shown
  without its status;
* ``status`` is ``accepted`` (the gate or a person accepted it), ``needs_check`` (a person must
  look) or ``rejected``; values the gate never judged (identity and doctor fields read from the
  page) are ``not_gated`` and say so;
* a doubtful value is never presented as final: the document ``status`` is ``needs_check`` while
  any value is;
* unknown is ``null``, never a guess, and what the system does not extract yet is listed in
  ``not_extracted`` instead of being left out silently;
* the same document always gives the same bytes (no timestamps, fixed key order).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text

from .. import repo
from ..db import session_scope

SCHEMA_VERSION = "result.placeholder.v0"
NOTICE = ("Read by a machine. Values marked needs a check must be verified by a person. "
          "Not for diagnosis.")        # PLACEHOLDER wording: the owner and a clinician approve the real text

ACCEPTED = {"auto_accepted", "clinician_confirmed", "corrected"}
NEEDS_CHECK = {"pending", "in_review"}
# what the extraction does not produce yet (docs/upload-screen.md); kept visible, not hidden
NOT_EXTRACTED = ["doctor.designation", "organisation.name", "organisation.address",
                 "patient.address", "patient.phone", "patient.guardian", "lab_preparation",
                 "follow_up.interval"]
_FINISHED = {"validated", "normalized"}


def value(v: Any, status: str, reason: str | None = None, confidence: float | None = None) -> dict:
    return {"value": v, "status": status,
            "reason": reason, "confidence": None if confidence is None else round(float(confidence), 3)}


def _num(x: Any) -> float | int | None:
    if x is None:
        return None
    f = float(x)
    return int(f) if f == int(f) else round(f, 6)


def _fact_status(fact: dict[str, Any]) -> tuple[str, str | None]:
    state = fact.get("review_state")
    if state in ACCEPTED:
        return "accepted", None
    if state == "rejected":
        return "rejected", fact.get("review_note")
    return "needs_check", fact.get("review_note") or "waiting for a person to check it"


def _item(fact: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    status, reason = _fact_status(fact)
    return {"fact_id": str(fact["id"]), "text": fact.get("local_text"), **fields,
            "status": status, "reason": reason,
            "confidence": None if fact.get("confidence_overall") is None
            else round(float(fact["confidence_overall"]), 3)}


def _medication(f: dict[str, Any]) -> dict[str, Any]:
    d = f.get("medication") or {}
    return _item(f, {
        "drug": d.get("drug_text") or f.get("local_text"),
        "strength": _num(d.get("strength_num")), "strength_unit": d.get("strength_unit"),
        "dose": _num(d.get("dose_num")), "dose_unit": d.get("dose_unit_ucum"),
        "route": d.get("route"), "frequency": d.get("frequency_code"),
        "duration_days": d.get("duration_days"), "instructions": d.get("instructions")})


def _lab(f: dict[str, Any]) -> dict[str, Any]:
    return _item(f, {
        "name": f.get("local_text"), "value": _num(f.get("value_num")),
        "value_text": f.get("value_text"), "unit": f.get("value_unit_ucum"),
        "ref_low": _num(f.get("ref_range_low")), "ref_high": _num(f.get("ref_range_high")),
        "ref_text": f.get("ref_range_text"), "flag": f.get("abnormal_flag"),
        "code": f.get("code"), "code_system": f.get("code_system")})


def _vital(f: dict[str, Any]) -> dict[str, Any]:
    return _item(f, {"name": f.get("local_text"), "value": _num(f.get("value_num")),
                     "value_text": f.get("value_text"), "unit": f.get("value_unit_ucum")})


def _plain(f: dict[str, Any]) -> dict[str, Any]:
    return _item(f, {})


_BUCKETS = {"medication": ("medications", _medication), "lab_result": ("lab_tests", _lab),
            "vital_sign": ("vitals", _vital), "condition": ("diagnoses", _plain),
            "advice": ("advice", _plain)}


@dataclass
class ResultInputs:
    """Everything the JSON is built from (read once, so the build itself is a pure function)."""

    document: dict[str, Any]
    classification: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)       # latest extraction payload
    facts: list[dict[str, Any]] = field(default_factory=list)   # current facts, medication detail merged


def _quality(pages: list[dict[str, Any]], doc: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    seen = False
    for p in pages:
        q = (p.get("preproc") or {}).get("quality")
        if not q:
            continue
        seen = True
        reasons += [f"page {p['page_no']}: {r}" for r in q.get("reasons") or []]
        warnings += [f"page {p['page_no']}: {w}" for w in q.get("warnings") or []]
    if doc.get("status") == "quality_hold" and not reasons and doc.get("error_detail"):
        reasons = [str(doc["error_detail"])]
    return {"checked": seen, "passed": not reasons if (seen or reasons) else None,
            "reasons": reasons, "warnings": warnings}


def _read(v: Any) -> dict[str, Any]:
    """A value read from the page that the confidence gate does not judge (identity, doctor)."""
    return value(v if v not in ("", None) else None, "not_gated",
                 "read from the page; not checked by the confidence gate" if v not in ("", None) else None)


def build_result(inp: ResultInputs) -> dict[str, Any]:
    doc, payload = inp.document, inp.payload or {}
    patient = payload.get("patient") or {}
    prescriber = payload.get("prescriber") or {}
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in _BUCKETS.values()}
    other: list[dict[str, Any]] = []
    for f in inp.facts:
        name, build = _BUCKETS.get(f.get("fact_type") or "", ("other", _plain))
        (buckets.get(name) if name != "other" else other).append(build(f))      # type: ignore[union-attr]
    items = [i for lst in (*buckets.values(), other) for i in lst]
    n_check = sum(1 for i in items if i["status"] == "needs_check")
    quality = _quality(inp.pages, doc)

    if doc.get("status") == "quality_hold":
        status = "held_for_rescan"
    elif doc.get("status") == "error":
        status = "error"
    elif doc.get("status") not in _FINISHED:
        status = "processing"
    elif n_check or any(i["status"] == "rejected" for i in items):
        status = "needs_check"
    elif not items:
        status = "incomplete"          # finished, but nothing could be read
    else:
        status = "complete"

    follow_up = payload.get("follow_up")
    cls = inp.classification or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": str(doc["id"]),
        "filename": doc.get("original_filename"),
        "source": "upload_screen",
        "doc_type": cls.get("doc_type"),
        "is_handwritten": cls.get("is_handwritten"),
        "page_count": doc.get("page_count"),
        "status": status,
        "needs_check_count": n_check,
        "quality": quality,
        "patient": {"name": _read(patient.get("name")), "age_text": _read(patient.get("age_text")),
                    "sex": _read(patient.get("sex")), "mrn": _read(patient.get("mrn"))},
        "doctor": {"name": _read(prescriber.get("name")), "reg_no": _read(prescriber.get("reg_no")),
                   "department": _read(prescriber.get("department"))},
        **buckets,
        "other": other,
        "follow_up": _read(follow_up if isinstance(follow_up, str) else None),
        "not_extracted": NOT_EXTRACTED,
        "notice": NOTICE,
    }


def to_bytes(result: dict[str, Any]) -> bytes:
    """The one canonical serialisation: the screen shows it and the download is these exact bytes."""
    return (json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def gather(sess: Any, document_id: str) -> ResultInputs | None:
    doc = repo.get_document(sess, document_id)
    if not doc:
        return None
    facts = repo.list_clinical_facts(sess, document_id=document_id)
    for f in facts:
        if f["fact_type"] == "medication":
            f["medication"] = repo.get_medication_detail(sess, f["id"])
    payload = sess.execute(
        text("SELECT payload FROM extraction WHERE document_id = :d ORDER BY created_at DESC LIMIT 1"),
        {"d": document_id}).scalar_one_or_none()
    return ResultInputs(doc, repo.get_doc_classification(sess, document_id),
                        repo.list_document_pages(sess, document_id),
                        payload if isinstance(payload, dict) else {}, facts)


class OutputConnector(Protocol):
    name: str

    def render(self, document_id: str) -> dict[str, Any] | None: ...


class JsonPlaceholderConnector:
    name = "json_placeholder"

    def render(self, document_id: str) -> dict[str, Any] | None:
        with session_scope() as sess:
            inp = gather(sess, document_id)
        return build_result(inp) if inp else None


_CONNECTORS: dict[str, type] = {JsonPlaceholderConnector.name: JsonPlaceholderConnector}


def get_connector(name: str | None = None) -> OutputConnector:
    from ..config import settings

    key = name or settings.output_connector
    if key not in _CONNECTORS:
        raise ValueError(f"unknown output connector {key!r} (known: {', '.join(sorted(_CONNECTORS))})")
    return _CONNECTORS[key]()
