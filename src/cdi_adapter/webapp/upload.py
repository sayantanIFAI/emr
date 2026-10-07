"""Upload screen rules (UP-S1): what may be sent, in plain words. No database, no web framework.

Everything a person can get wrong on the upload screen is checked here, on the server, and each
refusal is one plain sentence a front-desk person can act on (the screen shows it as written).
The browser checks the same limits first (``GET /api/upload/limits``) so it can say it sooner, but
the browser is never trusted.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..ingest.assemble import assemble_images_pdf, sniff_upload_type
from ..logging import get_logger

log = get_logger(__name__)

GROUPINGS = ("separate", "one_document")
_EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif"}
_PATIENT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-_./@]*$")
_PATIENT_REF_MAX = 40
_NAME_MAX = 80


class UploadError(ValueError):
    """A refusal with a plain-language message and the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def limits() -> dict[str, object]:
    """What the screen may tell the person before they press Send (settings, never hard-coded)."""
    return {
        "accept": [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"],
        "max_files": settings.upload_max_files,
        "max_file_mb": round(settings.upload_max_file_bytes / 1_000_000, 1),
        "max_total_mb": round(settings.upload_max_total_bytes / 1_000_000, 1),
        # the server's own rule: a picture with a shorter side than this is held for a retake
        "min_short_side_px": settings.quality_min_short_side_px,
    }


def normalize_abha(value: str | None) -> str | None:
    """``None`` for an empty field; otherwise the 14-digit ABHA number as ``XX-XXXX-XXXX-XXXX``."""
    text = (value or "").strip()
    if not text:
        return None
    digits = re.sub(r"[ \-]", "", text)
    if not (digits.isascii() and digits.isdigit() and len(digits) == 14):
        raise UploadError("The ABHA number must have 14 digits, for example 14-1111-2222-3333.")
    return f"{digits[:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:]}"


def clean_patient_ref(value: str | None) -> str | None:
    """A CareFlow id, ABHA number or mobile number: short, plain characters only."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > _PATIENT_REF_MAX or not _PATIENT_REF.match(text):
        raise UploadError(
            f"The patient reference can use letters, numbers, spaces and - _ . / @ only, "
            f"up to {_PATIENT_REF_MAX} characters.")
    return text


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{0,19}$")


def clean_token(value: str | None, *, required: bool = True) -> str | None:
    """The token number from the front desk: letters, numbers and - _ / only, up to 20 characters."""
    text = (value or "").strip()
    if not text:
        if required:
            raise UploadError("Please enter the token number.")
        return None
    if not _TOKEN.match(text):
        raise UploadError("The token number can use letters, numbers and - _ / only, up to 20 characters.")
    return text


def clean_phone(value: str | None, *, required: bool = True) -> str | None:
    """A 10-digit Indian mobile number (a leading +91, 91 or 0 is accepted and dropped); returned as the 10 digits."""
    text = (value or "").strip()
    if not text:
        if required:
            raise UploadError("Please enter the 10-digit mobile number.")
        return None
    digits = re.sub(r"[ \-().]", "", text)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not (digits.isascii() and digits.isdigit() and len(digits) == 10 and digits[0] in "6789"):
        raise UploadError("The mobile number must be 10 digits and start with 6, 7, 8 or 9.")
    return digits


def clean_person_name(value: str | None) -> str:
    """A patient's name typed by a person: letters of any script (with their combining marks), spaces and . ' - only; 2 to 80
    characters, starting with a letter, with at least two letters. Anything else (digits, markup, control or direction
    characters) is refused."""
    import unicodedata

    text = " ".join((value or "").split())
    letters = 0
    ok = 2 <= len(text) <= 80 and unicodedata.category(text[0]).startswith("L")
    for ch in text if ok else "":
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            letters += 1
        elif not (cat.startswith("M") or ch in " .'-"):
            ok = False
            break
    if not ok or letters < 2:
        raise UploadError("Please type the patient's name using letters, spaces and . ' - only (2 to 80 characters).")
    return text


def display_name(name: str | None) -> str:
    """A file name safe to echo in a message or a log: no path, no control characters."""
    base = os.path.basename((name or "").replace("\\", "/")) or "document"
    # control characters and the invisible text-direction controls (a name that ends "gnp.exe" after U+202E
    # would display as "exe.png")
    return re.sub(r"[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", base)[:_NAME_MAX] or "document"


class RateLimiter:
    """At most ``CDI_UPLOAD_RATE_PER_MINUTE`` sends per minute (sliding window, per process). 0 = no limit.
    Slows a flood from one sign-in; it is not a substitute for a gateway-level limit."""

    def __init__(self) -> None:
        self._hits: list[float] = []
        self._lock = threading.Lock()

    def check(self, now: float | None = None) -> None:
        cap = settings.upload_rate_per_minute
        if cap <= 0:
            return
        t = time.time() if now is None else now
        with self._lock:
            self._hits = [h for h in self._hits if t - h < 60.0]
            if len(self._hits) >= cap:
                raise UploadError("Too many sends in a short time. Please wait a minute and try again.", 429)
            self._hits.append(t)


LIMITER = RateLimiter()


@dataclass
class Item:
    """One document the job will read. ``parts`` = the untouched originals it was built from."""

    name: str
    data: bytes
    mime: str
    parts: list[tuple[str, bytes]] | None = None


def check_files(files: list[tuple[str, bytes]]) -> list[Item]:
    """Count, size and TYPE (from the bytes, not the name) of every file, in plain words."""
    if not files:
        raise UploadError("Please add at least one photo, PDF or scan.")
    if len(files) > settings.upload_max_files:
        raise UploadError(f"You can send up to {settings.upload_max_files} files at once.")
    total = 0
    items: list[Item] = []
    for raw_name, data in files:
        name = display_name(raw_name)
        if not data:
            raise UploadError(f"'{name}' is empty. Please add it again.")
        if len(data) > settings.upload_max_file_bytes:
            raise UploadError(
                f"'{name}' is larger than {settings.upload_max_file_bytes // 1_000_000} MB.", 413)
        total += len(data)
        if total > settings.upload_max_total_bytes:
            raise UploadError(
                f"Together the files are larger than {settings.upload_max_total_bytes // 1_000_000}"
                f" MB. Please send fewer pages at once.", 413)
        mime = sniff_upload_type(data)
        if mime is None or mime not in settings.upload_mime_types:
            raise UploadError(f"Please add a photo, PDF or scan. '{name}' is not one.")
        items.append(Item(name, data, mime))
    return items


def prepare(files: list[tuple[str, bytes]], grouping: str = "separate") -> list[Item]:
    """Validate and, for ``one_document``, combine the pictures into ONE prescription.

    ``separate`` keeps today's behaviour: every file is its own document. ``one_document`` makes
    the pictures the pages of a single prescription (one document, one result); a PDF already holds
    its own pages, so it cannot be mixed in."""
    if grouping not in GROUPINGS:
        raise UploadError("Please choose whether the pages are one prescription or separate files.")
    items = check_files(files)
    if grouping == "separate" or len(items) == 1:
        return items
    if any(i.mime == "application/pdf" for i in items):
        raise UploadError(
            "A PDF already holds all of its pages. Send it on its own, or choose 'separate files'.")
    try:
        pdf = assemble_images_pdf([i.data for i in items])
    except Exception as exc:  # a damaged picture must read as a plain message
        log.warning("assemble_failed", error=type(exc).__name__)
        raise UploadError(
            "One of the pictures could not be read. Please retake it and try again.") from exc
    stem = os.path.splitext(items[0].name)[0] or "prescription"
    return [Item(f"{stem} ({len(items)} pages).pdf", pdf, "application/pdf",
                 [(i.name, i.data) for i in items])]


def store_parts(document_id: str, sha256: str, parts: list[tuple[str, bytes]]) -> None:
    """Keep every original upload, byte for byte, beside the assembled document (never only the
    derived PDF), and audit their hashes."""
    listing = []
    for n, (name, raw) in enumerate(parts, start=1):
        mime = sniff_upload_type(raw) or "application/octet-stream"
        key = f"documents/{sha256[:2]}/{sha256}/parts/{n:02d}{_EXT.get(mime, '.bin')}"
        storage.put_bytes(key, raw, mime)
        listing.append({"n": n, "name": display_name(name), "mime": mime, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()})
    with session_scope() as sess:
        repo.write_audit(sess, actor="webapp", action="update", entity="source_document",
                         entity_id=document_id, detail={"original_parts": listing})

