"""Pictures for the upload screen: the prescription page itself and the cut-out of the patient's name line.

The front desk confirms a handwritten name by LOOKING at the handwriting, so the screen shows the page (click to enlarge) and,
beside the confirm box, the name line cut out of it. Everything here only reads what was stored; nothing is changed.
"""
from __future__ import annotations

import uuid
from typing import Any

import cv2
import numpy as np

from .. import repo, storage
from ..db import session_scope

MAX_WIDTH = 2400
VIEWS = ("page", "original")


def _png(arr: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".png", arr)
    if not ok:
        raise ValueError("could not encode the picture")
    return enc.tobytes()


def _valid(document_id: str) -> str | None:
    try:
        return str(uuid.UUID(document_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _source_uri(page: dict[str, Any]) -> str:
    """The colour page as it was cut out and straightened (what the readers were given before any contrast change), else the
    normalized copy."""
    pre = page.get("preproc")
    return (pre.get("src_uri") if isinstance(pre, dict) else None) or page["image_uri"]


def page_image(document_id: str, page_no: int = 1, view: str = "page", width: int | None = None) -> tuple[bytes, str] | None:
    """``(picture bytes, media type)`` of one page, or None. ``view``: ``page`` = the colour page the system read; ``original`` =
    the file as it was uploaded (when it is a picture; a PDF falls back to the page). ``width`` shrinks it (a thumbnail)."""
    doc_id = _valid(document_id)
    if doc_id is None or view not in VIEWS:
        return None
    with session_scope() as sess:
        doc = repo.get_document(sess, doc_id)
        pages = {p["page_no"]: p for p in repo.list_document_pages(sess, doc_id)}
    if not doc or page_no not in pages:
        return None
    data, mime = None, "image/png"
    if view == "original" and (doc.get("mime_type") or "").startswith("image/") and len(pages) == 1:
        data, mime = storage.get_bytes(storage.key_from_uri(doc["object_uri"])), doc["mime_type"]
    if data is None:
        data = storage.get_bytes(storage.key_from_uri(_source_uri(pages[page_no])))
    if width:
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None and arr.shape[1] > width:
            w = max(60, min(int(width), MAX_WIDTH))
            arr = cv2.resize(arr, (w, max(1, int(arr.shape[0] * w / arr.shape[1]))), interpolation=cv2.INTER_AREA)
            return _png(arr), "image/png"
    return data, mime


def name_crop(document_id: str) -> bytes | None:
    """The patient's name line cut out of page 1 and enlarged, or None when it cannot be found."""
    from ..extract import resolve_llm

    doc_id = _valid(document_id)
    if doc_id is None:
        return None
    with session_scope() as sess:
        doc = repo.get_document(sess, doc_id)
        pages = repo.list_document_pages(sess, doc_id)
        blocks = repo.list_ocr_blocks(sess, doc_id)
    if not doc or not pages:
        return None
    first = sorted(pages, key=lambda p: p["page_no"])[0]
    name = doc.get("name_read") or doc.get("patient_name")
    on_first = [b for b in blocks if str(b.get("page_id")) == str(first["id"])]
    crops = resolve_llm.name_crops(storage.get_bytes(storage.key_from_uri(_source_uri(first))), on_first, name)
    return crops[1] if len(crops) > 1 else (crops[0] if crops else None)
