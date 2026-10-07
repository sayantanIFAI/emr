"""Is this region writing at all? (the reading-side half of "do not read the bedspread")

The line detector works on ink-like blobs, and in a phone photo of a page on a patterned bedspread, a table or a
hand, the edge of the paper, a fold of fabric or a shadow is also an "ink-like blob". Sending those to the readers
wastes time and, worse, invites a reader to invent words for something that is not text. This module decides, from
two cheap measurements per region, whether there is pen or print on PAPER in the box:

* **thin ink**: the share of the box covered by thin dark strokes (black-hat of a small ellipse). Pen strokes and
  print are thin; a fabric edge, a shadow or the page border are wide, so they barely register;
* **paper colour distance**: how far the box's own background (the lighter half of its pixels) is from the colour of
  the page's paper, in CIELAB. Orange / green fabric is far; paper in a blue-ish phone light is near.

A region that fails either test is marked ``non_text`` and keeps its measurements: it is never read and never shown
as text, but it is not thrown away silently (the record says why). Nothing here looks at the content of the writing.
The limits are PLACEHOLDERS from three real photos; they are settings, and the three-photo check is in
docs/handwriting-reading.md.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..config import settings

NON_TEXT = "non_text"


def paper_colour(src_bgr: np.ndarray) -> np.ndarray:
    """The page's paper colour (CIELAB median of the brighter 40% of the picture): robust to a coloured light."""
    lab = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    bright = lab[..., 0] >= np.percentile(lab[..., 0], 60)
    return np.median(lab[bright], axis=0)


def thin_ink_map(gray: np.ndarray) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k) > settings.nontext_blackhat_threshold


class PageEvidence:
    """Computed once per page, then asked about each region."""

    def __init__(self, src_bgr: np.ndarray) -> None:
        self.gray = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2GRAY)
        self.lab = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
        self.paper = paper_colour(src_bgr)
        self.thin = thin_ink_map(self.gray)

    def measure(self, bbox: list[int]) -> dict[str, float]:
        # paper_distance compares COLOUR only (the a and b channels of CIELAB), not brightness: a shadow or a lamp makes
        # one half of a page darker than the other, but it stays paper-coloured; fabric and skin change the colour.
        x0, y0, x1, y1 = (int(v) for v in bbox)
        x0, y0 = max(0, x0), max(0, y0)
        t = self.thin[y0:y1, x0:x1]
        px = self.lab[y0:y1, x0:x1].reshape(-1, 3)
        if t.size == 0 or px.size == 0:
            return {"thin_ink_frac": 0.0, "paper_distance": 99.0}
        light = px[px[:, 0] >= np.percentile(px[:, 0], 50)]
        return {"thin_ink_frac": round(float(t.mean()), 4),
                "paper_distance": round(float(np.linalg.norm(np.median(light, axis=0)[1:] - self.paper[1:])), 1)}


def judge(m: dict[str, float], bbox: list[int]) -> str | None:
    """``None`` = looks like writing on paper; otherwise the plain reason it is set aside."""
    h = bbox[3] - bbox[1]
    w = bbox[2] - bbox[0]
    if m["thin_ink_frac"] < settings.nontext_min_thin_ink:
        return f"almost no thin pen or print strokes in it ({m['thin_ink_frac']:.1%} of the box)"
    if m["paper_distance"] > settings.nontext_max_paper_distance:
        return f"its background is not the page's paper colour (distance {m['paper_distance']:.0f}): fabric, table or hand"
    if h < settings.nontext_min_height_px or w < settings.nontext_min_width_px:
        return f"too small to be a line of writing ({w}x{h} px)"
    return None


def classify_regions(regions: list[Any], src_bgr: np.ndarray) -> dict[str, int]:
    """Mark every region that is not writing on paper as ``non_text`` (in place) and say how many. A region that
    RapidOCR read confidently as print is trusted as text whatever the colour (a coloured logo is still text)."""
    ev = PageEvidence(src_bgr)
    n = 0
    for r in regions:
        m = ev.measure(r.bbox)
        r.features.update(m)
        confident_print = (r.kind == "printed" and r.features.get("ocr_mean_conf", 0.0) >= 0.85
                           and r.features.get("ocr_coverage", 0.0) >= 0.6)
        why = None if confident_print else judge(m, r.bbox)
        if why:
            r.features["non_text_reason"] = why
            r.features["was_kind"] = r.kind
            r.kind = NON_TEXT
            n += 1
    return {"non_text": n, "regions": len(regions)}
