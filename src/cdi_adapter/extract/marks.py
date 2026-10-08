"""Pen marks on a PRE-PRINTED list of tests: a tick, a circle, a cross or a scratch by a printed name means the doctor ordered it.

Many pads carry a printed checklist (FPG, CREATININE, LIPID PROFILE / Free T4, TSH / URINE RE, ACR ...) and the doctor orders by marking
some of the names. The printed names alone are not orders; the marked ones are. MEASURED on a real photo: the printed text is blue, the
doctor's marks are dark pen that is not blue; with the blue (and the soft halo around it) taken away, the pen strokes stand alone.

What this module does, with no model:
- ``pen_mask``  the dark ink that is not the blue print;
- ``printed_blue_line``  a line the reader called printed whose own ink is blue (a handwritten blue line is NOT taken for a printed one:
  the reader's "printed" label alone is not reliable);
- ``token_marked``  whether pen strokes touch the part of the line where a test name stands.
It never removes anything on its own: the caller drops an unmarked printed name only on a page where at least one printed name IS marked
(the doctor used the checklist convention), so a blank pad or a page whose colours cannot tell print from pen behaves as before."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

_BLUE_B = -5            # Lab b below this is blue ink (print on these pads); pen ink is near neutral
_MIN_AREA_FRAC = 0.00006


def pen_mask(img_bgr: np.ndarray) -> np.ndarray:
    """A boolean mask of ink that is clearly dark and not blue, with the soft halo around blue print removed."""
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    L = lab[..., 0].astype(np.int16)
    b = lab[..., 2].astype(np.int16) - 128
    paper = np.percentile(L, 85)
    ink = L < paper - 35
    blue = ink & (b < _BLUE_B)
    near_blue = cv2.dilate(blue.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    pen = (ink & ~near_blue).astype(np.uint8)
    pen = cv2.morphologyEx(pen, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    min_area = max(30, int(_MIN_AREA_FRAC * pen.size))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(pen, 8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[labels]


def _box(b: dict[str, Any], shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    try:
        x0, y0, x1, y1 = (int(float(v)) for v in b["bbox"][:4])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    h, w = shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    return (x0, y0, x1, y1) if x1 - x0 >= 8 and y1 - y0 >= 6 else None


def printed_blue_line(img_bgr: np.ndarray, block: dict[str, Any]) -> bool:
    """True when the reader called the line printed AND its own ink is blue (the pad's print colour)."""
    if (block.get("recognition") or {}).get("state") != "printed":
        return False
    box = _box(block, img_bgr.shape[:2])
    if box is None:
        return False
    x0, y0, x1, y1 = box
    lab = cv2.cvtColor(img_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2LAB)
    L = lab[..., 0]
    thr, _ = cv2.threshold(L, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = L < thr
    if int(ink.sum()) < 30:
        return False
    return float(np.median(lab[..., 2][ink]) - 128) < _BLUE_B


class Marks:
    """The pen strokes of a page and which printed line each belongs to: a stroke belongs to the ONE printed line it is nearest to (a tick
    between two lines is not credited to both)."""

    def __init__(self, img_bgr: np.ndarray, blocks: list[dict[str, Any]]) -> None:
        self.pen = pen_mask(img_bgr)
        n, _labels, stats, cents = cv2.connectedComponentsWithStats(self.pen.astype(np.uint8), 8)
        self.comps = [(int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]), float(cents[i][0]), float(cents[i][1]))
                      for i in range(1, n)]
        shape = img_bgr.shape[:2]
        self.lines: dict[int, tuple[int, int, int, int]] = {}
        for b in blocks:
            if printed_blue_line(img_bgr, b):
                box = _box(b, shape)
                if box:
                    self.lines[id(b)] = box

    def is_printed(self, block: dict[str, Any]) -> bool:
        return id(block) in self.lines

    @staticmethod
    def _dist(cx: float, cy: float, box: tuple[int, int, int, int]) -> float:
        x0, y0, x1, y1 = box
        return float(np.hypot(max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1)))

    def marked(self, block: dict[str, Any], text: str, token: str) -> bool:
        """Whether a pen stroke that belongs to this line touches the stretch where ``token`` stands (its share of the line's characters
        along the line's box, widened by half a line height sideways and above / below)."""
        box = self.lines.get(id(block))
        pos = text.casefold().find((token or "").casefold())
        if box is None or not token or pos < 0:
            return False
        x0, y0, x1, y1 = box
        h = max(y1 - y0, 12)
        n = max(len(text), 1)
        tx0 = x0 + int((x1 - x0) * pos / n)
        tx1 = x0 + int((x1 - x0) * (pos + len(token)) / n)
        zx0, zx1, zy0, zy1 = tx0 - h // 2, tx1 + h // 2, y0 - h // 2, y1 + h // 2
        for cx0, cy0, cw, ch, cx, cy in self.comps:
            if cx0 > zx1 or cx0 + cw < zx0 or cy0 > zy1 or cy0 + ch < zy0:
                continue
            mine = self._dist(cx, cy, box)
            if all(mine <= self._dist(cx, cy, other) for other in self.lines.values()):
                return True
        return False
