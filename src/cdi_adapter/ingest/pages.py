"""Page rendering + image normalization.

- PDFs -> one PNG per page at a target DPI (pypdfium2 / PDFium, Apache-2.0 / BSD-3).
- Images -> a single normalized PNG (Pillow; EXIF orientation applied, never through PDFium).
- Normalization: grayscale -> deskew (min-area-rect on ink pixels) -> optional
  denoise -> CLAHE contrast. The ORIGINAL bytes are never altered; these are
  derived render artifacts used by OCR/VLM downstream.
"""
from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

from .._cpu import THREADS_PER_TASK
from ..config import settings

try:
    cv2.setNumThreads(THREADS_PER_TASK)
except Exception:  # noqa: BLE001
    pass
from ..logging import get_logger

log = get_logger(__name__)

# A PDF page that would need more pixels than this at the render DPI is refused instead of
# allocating gigabytes (a 14400 x 14400 pt page at 200 dpi is ~1.6 gigapixels). A0 at 200 dpi is
# ~62 megapixels, so real documents are never affected.
MAX_PDF_PAGE_PIXELS = 100_000_000

# PDFium is not thread-safe (pypdfium2 README): the API, the worker pool and Celery all ingest on
# threads, so every call into the library is serialised by this lock. PNG encoding is the slow
# part and happens outside it.
_PDFIUM_LOCK = threading.Lock()

# Intermediate page PNGs are read once by normalize_with_source() and thrown away, so they are
# written at the fastest zlib level (measured on a local PC: level 6 / 1 = 696 / 240 ms per page).
INTERMEDIATE_PNG_LEVEL = 1


class PdfReadError(ValueError):
    """The PDF is corrupt, password-protected, zero-sized or too large to render.
    A property of the file, so retrying cannot help (the listener classifies it as data)."""


@dataclass
class RenderedPage:
    page_no: int
    png_bytes: bytes             # normalised (gray, denoised, CLAHE) - what RapidOCR/classify read
    width_px: int
    height_px: int
    dpi: int
    preproc: dict[str, Any] = field(default_factory=dict)
    # pixel-faithful colour render in the SAME coordinates as png_bytes (deskew applied, no
    # filtering). Line crops and grounding are cut from this - never from the filtered image.
    src_png: bytes | None = None


def _pil_to_png(img: Image.Image, compress_level: int = 6) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=compress_level)
    return buf.getvalue()


def _np_to_png(arr: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".png", arr)
    if not ok:  # pragma: no cover
        raise RuntimeError("cv2.imencode failed")
    return enc.tobytes()


def _estimate_skew_deg(gray: np.ndarray) -> float:
    """Angle (deg) to rotate the page so text lines become horizontal."""
    inv = cv2.bitwise_not(gray)
    thr = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if coords.shape[0] < 50:
        return 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    # OpenCV returns angle in (-90, 0]; normalize to a small correction
    if angle < -45:
        angle = 90 + angle
    return float(-angle)


def _rotate(arr: np.ndarray, angle_deg: float) -> np.ndarray:
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    return cv2.warpAffine(
        arr, m, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def normalize_image(png_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    """Return (normalized_png, preproc_metadata)."""
    norm, _src, meta = normalize_with_source(png_bytes)
    return norm, meta


def normalize_with_source(png_bytes: bytes) -> tuple[bytes, bytes, dict[str, Any]]:
    """Return (normalized_png, source_png, meta). ``source_png`` is the colour render with the
    same deskew rotation but no denoise/CLAHE, so its pixels line up with OCR bboxes.
    ``meta['quality']`` is the E2-S12 gate verdict measured on the UNPROCESSED render."""
    from ..recognition.quality import assess

    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("could not decode page image")

    meta: dict[str, Any] = {"steps": []}
    if settings.quality_gate_mode != "off":
        meta["quality"] = assess(arr).as_dict()
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)

    skew = 0.0
    if settings.deskew_enabled:
        skew = _estimate_skew_deg(gray)
        if abs(skew) > 0.15 and abs(skew) <= settings.max_deskew_deg:
            gray = _rotate(gray, skew)
            arr = _rotate(arr, skew)
            meta["steps"].append("deskew")
    meta["skew_deg"] = round(skew, 3)

    if settings.denoise_enabled:
        gray = cv2.fastNlMeansDenoising(gray, h=7, templateWindowSize=7, searchWindowSize=21)
        meta["steps"].append("denoise")

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    meta["steps"].append("clahe")

    h, w = gray.shape[:2]
    meta["width_px"], meta["height_px"] = int(w), int(h)
    return _np_to_png(gray), _np_to_png(arr), meta


def _upright(img: Image.Image) -> Image.Image:
    """Apply the EXIF Orientation tag. A phone photo of a prescription is often stored sideways
    with only the tag saying so; the OCR models read the raw pixels and never see the tag.
    Best effort: a damaged EXIF block leaves the pixels as they are."""
    try:
        return ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        return img


def _open_pdf(raw: bytes) -> pdfium.PdfDocument:
    """Open ``raw`` (caller holds the PDFium lock); unreadable input becomes ``PdfReadError``."""
    try:
        doc = pdfium.PdfDocument(raw)
    except pdfium.PdfiumError as exc:
        # pypdfium2 4.x sets no usable error code for this case: fall back to the message text
        if (
            getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD
            or "password" in str(exc).lower()
        ):
            raise PdfReadError("PDF is password-protected") from exc
        raise PdfReadError(f"cannot open PDF: {exc}") from exc
    try:
        if doc.get_formtype() != pdfium_c.FORMTYPE_NONE:
            doc.init_forms()  # draw filled AcroForm / XFA field values onto the page image
    except pdfium.PdfiumError:
        pass  # form drawing is best effort: the page content itself still renders
    return doc


def _render_pdf_page(doc: pdfium.PdfDocument, index: int, scale: float) -> Image.Image:
    """Render page ``index`` (0-based) at ``scale`` (caller holds the PDFium lock)."""
    page = doc[index]
    try:
        width, height = page.get_size()  # points, after the page's own /Rotate
        if width <= 0 or height <= 0:
            raise PdfReadError(f"PDF page {index + 1} has no size")
        pixels = math.ceil(width * scale) * math.ceil(height * scale)
        if pixels > MAX_PDF_PAGE_PIXELS:
            raise PdfReadError(
                f"PDF page {index + 1} is too large to render ({pixels:,} pixels at this DPI)"
            )
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")  # convert() returns a copy: safe after close()
        finally:
            bitmap.close()
    except pdfium.PdfiumError as exc:
        raise PdfReadError(f"cannot render PDF page {index + 1}: {exc}") from exc
    finally:
        page.close()


def render_pdf_pngs(raw: bytes, dpi: int) -> list[bytes]:
    """One intermediate PNG per page (at most ``max_pages``). A page is ``ceil(points * dpi / 72)``
    pixels on each side."""
    # PDF user space is 72 units per inch. The tiny factor stops float rounding from adding a pixel
    # when points * dpi / 72 is an exact integer (US Letter at 150 dpi is 1650 px, not 1651).
    scale = dpi / 72.0 * (1 - 1e-9)
    with _PDFIUM_LOCK:
        doc = _open_pdf(raw)
        count = min(len(doc), settings.max_pages)
    pngs: list[bytes] = []
    try:
        for index in range(count):
            with _PDFIUM_LOCK:
                image = _render_pdf_page(doc, index, scale)
            # outside the lock; one page in memory at a time
            pngs.append(_pil_to_png(image, INTERMEDIATE_PNG_LEVEL))
    finally:
        with _PDFIUM_LOCK:
            doc.close()
    return pngs


def render_pages(raw: bytes, mime_type: str, *, dpi: int | None = None) -> list[RenderedPage]:
    dpi = dpi or settings.page_dpi
    pages: list[RenderedPage] = []

    if mime_type == "application/pdf" or raw[:5] == b"%PDF-":
        for i, png in enumerate(render_pdf_pngs(raw, dpi)):
            norm, src, meta = normalize_with_source(png)
            meta["source"] = "pdf"
            pages.append(
                RenderedPage(
                    page_no=i + 1,
                    png_bytes=norm,
                    width_px=meta["width_px"],
                    height_px=meta["height_px"],
                    dpi=dpi,
                    preproc=meta,
                    src_png=src,
                )
            )
        return pages

    if mime_type.startswith("image/") or _looks_like_image(raw):
        img = Image.open(io.BytesIO(raw))
        if getattr(img, "n_frames", 1) > 1:  # multi-page TIFF
            for i in range(img.n_frames):
                img.seek(i)
                frame = _upright(img).convert("RGB")
                norm, src, meta = normalize_with_source(
                    _pil_to_png(frame, INTERMEDIATE_PNG_LEVEL))
                meta["source"] = "tiff"
                pages.append(
                    RenderedPage(i + 1, norm, meta["width_px"], meta["height_px"], dpi, meta, src)
                )
        else:
            frame = _upright(img).convert("RGB")
            norm, src, meta = normalize_with_source(_pil_to_png(frame, INTERMEDIATE_PNG_LEVEL))
            meta["source"] = "image"
            pages.append(RenderedPage(1, norm, meta["width_px"], meta["height_px"], dpi, meta, src))
        return pages

    raise ValueError(f"unsupported mime_type for rendering: {mime_type!r}")


def _looks_like_image(raw: bytes) -> bool:
    sigs = (b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"GIF8", b"BM")
    return any(raw.startswith(s) for s in sigs)


def make_thumbnail(png_bytes: bytes, max_side: int = 480) -> bytes:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    img.thumbnail((max_side, max_side))
    return _pil_to_png(img)
