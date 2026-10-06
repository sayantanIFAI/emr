"""Image quality gate (E2-S12) - OpenCV, CPU, runs on the page render BEFORE normalisation.

A page that is too blurred, too small, glared, clipped or whose text is too small is held with a
specific, actionable reason ("rescan: ...") and a stable reason CODE (``reason_codes``: the upload
screen maps each code to one plain sentence) instead of flowing into a low-confidence extraction.
Thresholds are config (CDI_QUALITY_*) and must be tuned on the gold set (E2-S4) so that
genuinely readable pages are not rejected; every metric is recorded either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..config import settings

_NORM_WIDTH = 1200   # measure blur at a fixed scale so DPI does not change the number

# stable identifiers (never reworded: UP-S2 and the reports key on them)
RESOLUTION_LOW, BLURRED, GLARE = "resolution_low", "blurred", "glare"
TOO_DARK, TEXT_TOO_SMALL = "too_dark", "text_too_small"
BLANK_PAGE, SIDEWAYS = "blank_page", "sideways_suspected"
REASON_CODES = (RESOLUTION_LOW, BLURRED, GLARE, TOO_DARK, TEXT_TOO_SMALL)

_TILE = 1000            # text height is measured on full-resolution tiles of this size
_TILES = 3              # the inkiest ones
_MIN_GLYPHS = 150       # fewer glyph-sized blobs than this: not enough text to judge its height


@dataclass
class QualityReport:
    passed: bool
    reasons: list[str] = field(default_factory=list)     # blocking, rescan-worthy (messages)
    warnings: list[str] = field(default_factory=list)    # recorded, not blocking
    metrics: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)    # one stable code per entry of ``reasons``
    warning_codes: list[str] = field(default_factory=list)   # one stable code per entry of ``warnings``

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": self.reasons, "reason_codes": self.reason_codes,
                "warnings": self.warnings, "warning_codes": self.warning_codes,
                "metrics": self.metrics}


def _to_gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def blur_variance(gray: np.ndarray) -> float:
    h, w = gray.shape[:2]
    if w > _NORM_WIDTH:
        gray = cv2.resize(gray, (_NORM_WIDTH, int(h * _NORM_WIDTH / w)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def glare_fraction(gray: np.ndarray) -> float:
    """Share of the page in large saturated blobs, counted only when the paper itself is
    not white (a photo): on a clean white scan saturation is the paper, not glare."""
    paper = float(np.percentile(gray, 60))
    if paper >= 240:
        return 0.0
    sat = (gray >= 252).astype(np.uint8)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(sat, connectivity=8)
    min_blob = 0.002 * gray.size
    area = sum(int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
               if stats[i, cv2.CC_STAT_AREA] >= min_blob)
    return area / float(gray.size)


def dark_fraction(gray: np.ndarray) -> float:
    return float((gray < 40).mean())


def ink_fraction(gray: np.ndarray) -> float:
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    return float((thr > 0).mean())


def _binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 31, 15)


def _clean_ink(gray: np.ndarray) -> np.ndarray:
    """Ink mask without long ruled lines / table borders."""
    from .regions import _remove_rules

    return _remove_rules(_binarize(gray))


def orientation_ratio(gray: np.ndarray) -> float | None:
    """How much more the page looks like VERTICAL lines of text than horizontal ones (>1: sideways).

    Letters are merged along each axis in turn and the ink profile across the merged lines is
    scored (variance over mean squared). The score is taken INSIDE the ink's bounding box, so blank
    margins, a left-aligned block or a narrow column cannot decide it. ``None`` = too little ink.
    Synthetic pages (full, left-aligned, narrow column, sparse, table): upright 0.08-0.31, turned
    by 90/270 degrees 3.2-12. A photo with a dark background and sensor noise is ambiguous (~1.0);
    the page-edge work of IM-S2 is what helps there. Cannot tell 90 from 270, nor 0 from 180."""
    h, w = gray.shape[:2]
    s = 1400 / max(h, w)
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    ink = cv2.morphologyEx(_clean_ink(g), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ys, xs = np.where(ink > 0)
    if ys.size < 200:
        return None
    y0, y1 = np.percentile(ys, [1, 99]).astype(int)
    x0, x1 = np.percentile(xs, [1, 99]).astype(int)
    ink = ink[y0:y1 + 1, x0:x1 + 1]
    hh, ww = ink.shape
    hc = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, ww // 25), 1)))
    vc = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, hh // 25))))

    def score(profile: np.ndarray) -> float:
        m = float(profile.mean())
        return float(profile.var()) / (m * m) if m > 0 else 0.0

    sh, sv = score(hc.sum(axis=1).astype(float)), score(vc.sum(axis=0).astype(float))
    return sv / sh if sh > 1e-9 else None


def looks_rotated_90(gray: np.ndarray) -> bool:
    ratio = orientation_ratio(gray)
    return ratio is not None and ratio > settings.quality_sideways_ratio


def text_height_px(gray: np.ndarray) -> tuple[float | None, int]:
    """Effective text height in pixels: the 85th percentile of glyph-blob heights (about 0.7 of the
    font size), and how many blobs it rests on. Measured at FULL resolution (a downscaled copy would
    hide exactly the tiny text this exists to catch) but only on the few inkiest tiles, so a 22 MP
    photo costs the same as a scan. ``(None, n)`` when there is too little text to judge."""
    h, w = gray.shape[:2]
    if max(h, w) <= 2 * _TILE:
        tiles = [gray]
    else:
        small = cv2.resize(gray, (max(1, w // 8), max(1, h // 8)), interpolation=cv2.INTER_AREA)
        step = _TILE // 8
        cells = []
        for y in range(0, small.shape[0] - step // 2, step):
            for x in range(0, small.shape[1] - step // 2, step):
                cells.append((float(small[y:y + step, x:x + step].std()), y * 8, x * 8))
        cells.sort(reverse=True)
        tiles = [gray[y:y + _TILE, x:x + _TILE] for _s, y, x in cells[:_TILES]]
    heights: list[int] = []
    for t in tiles:
        ink = cv2.morphologyEx(_clean_ink(t), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, _lab, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
        limit = max(20, t.shape[0] // 20)
        heights += [int(stats[i, cv2.CC_STAT_HEIGHT]) for i in range(1, n)
                    if 3 <= stats[i, cv2.CC_STAT_HEIGHT] <= limit and stats[i, cv2.CC_STAT_WIDTH] >= 2
                    and stats[i, cv2.CC_STAT_AREA] >= 8]
    if len(heights) < _MIN_GLYPHS:
        return None, len(heights)
    return float(np.percentile(heights, 85)), len(heights)


def assess(img: np.ndarray) -> QualityReport:
    gray = _to_gray(img)
    h, w = gray.shape[:2]
    m: dict[str, Any] = {"width_px": int(w), "height_px": int(h), "short_side_px": int(min(h, w)),
         "blur_var": round(blur_variance(gray), 2), "glare_frac": round(glare_fraction(gray), 4),
         "dark_frac": round(dark_fraction(gray), 4), "ink_frac": round(ink_fraction(gray), 4)}
    reasons: list[str] = []
    warnings: list[str] = []
    codes: list[str] = []
    wcodes: list[str] = []

    def hold(code: str, message: str) -> None:
        codes.append(code)
        reasons.append(message)

    if m["short_side_px"] < settings.quality_min_short_side_px:
        hold(RESOLUTION_LOW, f"resolution too low ({m['short_side_px']} px short side; need "
             f">= {settings.quality_min_short_side_px}) - rescan at 200 dpi or more")
    if m["ink_frac"] < 0.001:
        warnings.append("page looks blank")
        wcodes.append(BLANK_PAGE)
    elif m["blur_var"] < settings.quality_min_blur_var:
        hold(BLURRED, f"image too blurred (sharpness {m['blur_var']:.0f} < "
             f"{settings.quality_min_blur_var:.0f}) - hold the camera steady and rescan")
    if m["glare_frac"] > settings.quality_max_glare_frac:
        hold(GLARE, f"glare covers {m['glare_frac']:.0%} of the page - avoid direct light and rescan")
    if m["dark_frac"] > settings.quality_max_dark_frac:
        hold(TOO_DARK, f"{m['dark_frac']:.0%} of the page is too dark or clipped - rescan in better light")
    # effective text height, not file size: a huge photo taken from far away has tiny text
    m["text_height_px"], m["text_glyphs"] = (None, 0)
    if m["ink_frac"] >= 0.001:
        m["text_height_px"], m["text_glyphs"] = text_height_px(gray)
        th = m["text_height_px"]
        if th is not None:
            m["text_height_px"] = round(th, 1)
            if settings.quality_min_text_height_px and th < settings.quality_min_text_height_px:
                hold(TEXT_TOO_SMALL, f"text too small to read reliably (about {th:.0f} px tall; need "
                     f">= {settings.quality_min_text_height_px}) - move closer and retake")
    ratio = orientation_ratio(gray) if m["ink_frac"] >= 0.001 else None
    m["orientation_ratio"] = None if ratio is None else round(ratio, 2)
    rot = ratio is not None and ratio > settings.quality_sideways_ratio
    m["rotated_90_suspected"] = rot
    if rot:
        warnings.append("page may be rotated 90 degrees - upright scans read better")
        wcodes.append(SIDEWAYS)
    return QualityReport(passed=not reasons, reasons=reasons, warnings=warnings, metrics=m,
                         reason_codes=codes, warning_codes=wcodes)


def assess_png(png_bytes: bytes) -> QualityReport:
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return QualityReport(False, ["page image could not be decoded - rescan"], [], {},
                             ["undecodable"], [])
    return assess(arr)
