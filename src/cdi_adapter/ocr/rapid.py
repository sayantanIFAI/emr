from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .._cpu import THREADS_PER_TASK
from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

# OpenCV keeps its own thread pool; the env var is not always honoured.
try:
    cv2.setNumThreads(THREADS_PER_TASK)
except Exception:  # noqa: BLE001
    pass

_engine = None
_lock = threading.Lock()


@dataclass
class OcrLine:
    text: str
    bbox: list[int]          # [x0, y0, x1, y1]
    conf: float
    polygon: list[list[int]]  # 4 points


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                try:
                    _engine = RapidOCR(intra_op_num_threads=THREADS_PER_TASK,
                                       inter_op_num_threads=1)
                except TypeError:  # older rapidocr without the kwargs
                    _engine = RapidOCR()
                log.info("rapidocr_loaded", intra_op_threads=THREADS_PER_TASK)
    return _engine


def _poly_to_bbox(poly: Any) -> list[int]:
    pts = np.asarray(poly, dtype=float).reshape(-1, 2)
    return [int(pts[:, 0].min()), int(pts[:, 1].min()),
            int(pts[:, 0].max()), int(pts[:, 1].max())]


def run_rapidocr(png_bytes: bytes) -> list[OcrLine]:
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("could not decode page image for OCR")
    result, _elapse = _get_engine()(arr)
    lines: list[OcrLine] = []
    for item in result or []:
        poly, text, score = item[0], item[1], float(item[2])
        text = (text or "").strip()
        if not text or score < settings.ocr_min_conf:
            continue
        lines.append(
            OcrLine(
                text=text,
                bbox=_poly_to_bbox(poly),
                conf=round(score, 3),
                polygon=[[int(x), int(y)] for x, y in np.asarray(poly).reshape(-1, 2)],
            )
        )
    return order_reading(lines)


def order_reading(lines: list[OcrLine]) -> list[OcrLine]:
    """Top-to-bottom, left-to-right within a horizontal band."""
    if not lines:
        return lines
    heights = sorted((ln.bbox[3] - ln.bbox[1]) for ln in lines)
    med_h = max(heights[len(heights) // 2], 8)
    band = 0.7 * med_h

    def key(ln: OcrLine) -> tuple[int, int]:
        yc = (ln.bbox[1] + ln.bbox[3]) / 2
        return (int(yc // band), ln.bbox[0])

    return sorted(lines, key=key)
