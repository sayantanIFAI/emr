"""Image quality gate (E2-S12) - OpenCV, CPU, runs on the page render BEFORE normalisation.

A page that is too blurred, too small, glared or clipped is held with a specific,
actionable reason ("rescan: ...") instead of flowing into a low-confidence extraction.
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


@dataclass
class QualityReport:
    passed: bool
    reasons: list[str] = field(default_factory=list)     # blocking, rescan-worthy
    warnings: list[str] = field(default_factory=list)    # recorded, not blocking
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reasons": self.reasons, "warnings": self.warnings,
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


def looks_rotated_90(gray: np.ndarray) -> bool:
    """Text lines make the ROW ink profile far more variable than the column profile.
    If the column profile dominates, the page is probably on its side."""
    small = cv2.resize(gray, (400, int(400 * gray.shape[0] / max(1, gray.shape[1]))))
    ink = cv2.threshold(small, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1].astype(float)
    if ink.mean() < 0.002:
        return False
    rows, cols = ink.mean(axis=1), ink.mean(axis=0)
    return float(cols.var()) > 1.8 * float(rows.var())


def assess(img: np.ndarray) -> QualityReport:
    gray = _to_gray(img)
    h, w = gray.shape[:2]
    m = {"width_px": int(w), "height_px": int(h), "short_side_px": int(min(h, w)),
         "blur_var": round(blur_variance(gray), 2), "glare_frac": round(glare_fraction(gray), 4),
         "dark_frac": round(dark_fraction(gray), 4), "ink_frac": round(ink_fraction(gray), 4)}
    reasons: list[str] = []
    warnings: list[str] = []
    if m["short_side_px"] < settings.quality_min_short_side_px:
        reasons.append(f"resolution too low ({m['short_side_px']} px short side; need "
                       f">= {settings.quality_min_short_side_px}) - rescan at 200 dpi or more")
    if m["ink_frac"] < 0.001:
        warnings.append("page looks blank")
    elif m["blur_var"] < settings.quality_min_blur_var:
        reasons.append(f"image too blurred (sharpness {m['blur_var']:.0f} < "
                       f"{settings.quality_min_blur_var:.0f}) - hold the camera steady and rescan")
    if m["glare_frac"] > settings.quality_max_glare_frac:
        reasons.append(f"glare covers {m['glare_frac']:.0%} of the page - avoid direct light and rescan")
    if m["dark_frac"] > settings.quality_max_dark_frac:
        reasons.append(f"{m['dark_frac']:.0%} of the page is too dark or clipped - rescan in better light")
    rot = looks_rotated_90(gray) if m["ink_frac"] >= 0.001 else False
    m["rotated_90_suspected"] = rot
    if rot:
        warnings.append("page may be rotated 90 degrees - upright scans read better")
    return QualityReport(passed=not reasons, reasons=reasons, warnings=warnings, metrics=m)


def assess_png(png_bytes: bytes) -> QualityReport:
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return QualityReport(False, ["page image could not be decoded - rescan"], [], {})
    return assess(arr)
