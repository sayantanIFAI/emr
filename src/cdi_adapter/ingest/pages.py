"""Page rendering + image normalization.

- PDFs -> one PNG per page at a target DPI (PyMuPDF).
- Images -> a single normalized PNG.
- Normalization: grayscale -> deskew (min-area-rect on ink pixels) -> optional
  denoise -> CLAHE contrast. The ORIGINAL bytes are never altered; these are
  derived render artifacts used by OCR/VLM downstream.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)


@dataclass
class RenderedPage:
    page_no: int
    png_bytes: bytes
    width_px: int
    height_px: int
    dpi: int
    preproc: dict[str, Any] = field(default_factory=dict)


def _pil_to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
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
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("could not decode page image")

    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    meta: dict[str, Any] = {"steps": []}

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
    return _np_to_png(gray), meta


def render_pages(raw: bytes, mime_type: str, *, dpi: int | None = None) -> list[RenderedPage]:
    dpi = dpi or settings.page_dpi
    pages: list[RenderedPage] = []

    if mime_type == "application/pdf" or raw[:5] == b"%PDF-":
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            n = min(doc.page_count, settings.max_pages)
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            for i in range(n):
                pix = doc.load_page(i).get_pixmap(matrix=mat, alpha=False)
                png = pix.tobytes("png")
                norm, meta = normalize_image(png)
                meta["source"] = "pdf"
                pages.append(
                    RenderedPage(
                        page_no=i + 1,
                        png_bytes=norm,
                        width_px=meta["width_px"],
                        height_px=meta["height_px"],
                        dpi=dpi,
                        preproc=meta,
                    )
                )
        finally:
            doc.close()
        return pages

    if mime_type.startswith("image/") or _looks_like_image(raw):
        img = Image.open(io.BytesIO(raw))
        if getattr(img, "n_frames", 1) > 1:  # multi-page TIFF
            for i in range(img.n_frames):
                img.seek(i)
                frame = img.convert("RGB")
                norm, meta = normalize_image(_pil_to_png(frame))
                meta["source"] = "tiff"
                pages.append(
                    RenderedPage(i + 1, norm, meta["width_px"], meta["height_px"], dpi, meta)
                )
        else:
            frame = img.convert("RGB")
            norm, meta = normalize_image(_pil_to_png(frame))
            meta["source"] = "image"
            pages.append(RenderedPage(1, norm, meta["width_px"], meta["height_px"], dpi, meta))
        return pages

    raise ValueError(f"unsupported mime_type for rendering: {mime_type!r}")


def _looks_like_image(raw: bytes) -> bool:
    sigs = (b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"GIF8", b"BM")
    return any(raw.startswith(s) for s in sigs)


def make_thumbnail(png_bytes: bytes, max_side: int = 480) -> bytes:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    img.thumbnail((max_side, max_side))
    return _pil_to_png(img)
