"""Which way up is the page? Ask the printed text.

A phone photo of a page can be taken sideways or upside down. The ink-shape rule (``geometry.upright_score`` / ``decide_sideways``)
is a statistical guess and can be wrong in either direction (a real page was turned the wrong way by a wide margin and then every
handwritten word read as garbage). The reliable witness is the PRINTED text (a letterhead, an address, a footer): read the page
at each of the four turns with OCR's own per-line "flip" correction switched OFF, so upside-down or sideways print reads as
nonsense, and the turn where the print reads best is the upright one.

``vote`` returns that turn only when it is clearly better than the others; with no printed text, or no clear winner, it returns
``None`` and the caller keeps its other rule. Nothing here changes the picture.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import cv2
import numpy as np

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

SIDE = 1200                      # the page is read at this long side: enough for print, fast on a CPU


def read_score(lines: list[Any]) -> float:
    """How much real text was read: confident lines of letters, counted by letters (at most 20 per line)."""
    total = 0.0
    for ln in lines:
        text = getattr(ln, "text", "") or ""
        conf = float(getattr(ln, "conf", 0.0) or 0.0)
        letters = sum(ch.isalpha() for ch in text)
        if conf >= 0.5 and letters >= 3 and letters >= 0.6 * max(1, len(text.replace(" ", ""))):
            total += conf * min(letters, 20)
    return total


def _png(arr: np.ndarray) -> bytes:
    ok, enc = cv2.imencode(".png", arr)
    if not ok:
        raise ValueError("could not encode the picture")
    return enc.tobytes()


def _read(arr: np.ndarray, k: int, host: Any) -> float:
    turned = np.ascontiguousarray(np.rot90(arr, k))
    try:
        return read_score(host.rapid(_png(turned), use_cls=False))
    except Exception as exc:  # noqa: BLE001 - no OCR: no opinion
        log.warning("orient_ocr_failed", k=k, error=str(exc)[:160])
        return 0.0


def vote(arr: np.ndarray, host: Any | None = None, candidates: tuple[int, ...] = (0, 1, 2, 3)) -> tuple[int | None, dict[str, Any]]:
    """``(k, info)``: ``k`` is the ``numpy.rot90`` turn (0-3, counter-clockwise) that makes the page upright, or ``None`` when the
    printed text does not say. Only the ``candidates`` are read: (1, 3) when the page is known to be sideways and only the direction
    is in doubt, (0, 2) when only upside-down is in doubt (comparing two turns on the same axis separates them far better than
    comparing all four: MEASURED on a real photo, 1.5x against 1.2x). ``info`` carries the scores."""
    if host is None:
        from ..recognition.ocrhost_client import get_ocr_host

        host = get_ocr_host()
    h, w = arr.shape[:2]
    f = SIDE / max(h, w)
    small = cv2.resize(arr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1.0 else arr
    cands = list(candidates)
    with ThreadPoolExecutor(max_workers=len(cands)) as pool:
        got = list(pool.map(lambda k: _read(small, k, host), cands))
    scores = dict(zip(cands, got))
    order = sorted(cands, key=lambda k: -scores[k])
    best, second = order[0], order[1]
    info = {"scores": {str(k): round(s, 1) for k, s in scores.items()}, "best": best}
    if scores[best] < settings.orient_ocr_min_score or scores[best] < settings.orient_ocr_min_ratio * max(scores[second], 1e-9):
        info["decided"] = False
        return None, info
    info["decided"] = True
    return best, info
