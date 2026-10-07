"""Make a small or soft photo easier to read before anything reads it: larger, then sharper.

Two steps, both done with OpenCV and both guarded so they cannot make a page worse:

1. ``upscale``: a picture whose long side is below ``enhance_target_long_side`` is enlarged (bicubic, by at most
   ``enhance_max_scale``). A WhatsApp-sized photo (about 1000 px) has handwriting only a few pixels thick; the reader
   does better on the same picture at twice the size. The step is recorded in the page transform, so every box found
   later still maps back to the original picture.
2. ``sharpen``: an unsharp mask whose strength follows how soft the picture is measured to be (variance of the Laplacian
   at a fixed size), and none for a picture that is already crisp. Applied after the denoise so it does not amplify noise.
   Before it is kept, the result is checked: it must be measurably sharper, must not clip more of the page to pure
   black / white, and must still be the same picture (a blurred copy of it must match a blurred copy of the original).
   If any check fails the unsharpened picture is used and the reason is recorded.

Nothing here reads or invents content: only pixel values change, never what is where.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..config import settings

_REF_SIDE = 1000          # sharpness is compared at this long side, so a big scan and a small photo are measured alike


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian with the picture brought to a common size (a larger copy is always 'softer')."""
    h, w = gray.shape[:2]
    f = _REF_SIDE / max(h, w)
    g = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if abs(f - 1.0) > 0.02 else gray
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def upscale_factor(h: int, w: int) -> float:
    long_side = max(h, w)
    if not settings.enhance_enabled or long_side >= settings.enhance_target_long_side:
        return 1.0
    return float(min(settings.enhance_max_scale, settings.enhance_target_long_side / long_side))


def upscale(arr: np.ndarray, factor: float) -> np.ndarray:
    if factor <= 1.001:
        return arr
    return cv2.resize(arr, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def _unsharp(img: np.ndarray, sigma: float, amount: float) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def _strength(blur_var: float) -> float:
    """How hard to sharpen, 0 for a crisp picture. Soft pictures (low Laplacian variance) get the most."""
    lo, hi = settings.enhance_soft_below, settings.enhance_crisp_above
    if blur_var >= hi:
        return 0.0
    if blur_var <= lo:
        return 1.0
    return float((hi - blur_var) / (hi - lo))


def _clipped(gray: np.ndarray) -> float:
    return float(((gray <= 2) | (gray >= 253)).mean())


def _same_picture(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation of the two pictures after a heavy blur (their large-scale layout), 1.0 = identical."""
    sa = cv2.GaussianBlur(a, (0, 0), 6).astype(np.float32).ravel()
    sb = cv2.GaussianBlur(b, (0, 0), 6).astype(np.float32).ravel()
    sa -= sa.mean()
    sb -= sb.mean()
    d = float(np.linalg.norm(sa) * np.linalg.norm(sb))
    return float(np.dot(sa, sb) / d) if d > 0 else 1.0


def sharpen(gray: np.ndarray, colour: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """``(gray, colour, info)``: both sharpened by the same mask, or returned unchanged with ``info['applied']`` False and
    the reason. ``info`` carries the measured sharpness before and after (MEASURED on this picture, not a promise)."""
    before = sharpness(gray)
    info: dict[str, Any] = {"applied": False, "sharpness_before": round(before, 1)}
    if not settings.enhance_enabled:
        info["reason"] = "off"
        return gray, colour, info
    s = _strength(before)
    if s <= 0.0:
        info["reason"] = "already sharp"
        return gray, colour, info
    amount = settings.enhance_max_amount * s
    sigma = settings.enhance_sigma
    g2 = _unsharp(gray, sigma, amount)
    after = sharpness(g2)
    why = None
    if after < before * 1.05:
        why = "not sharper"
    elif _clipped(g2) > _clipped(gray) + settings.enhance_max_new_clipping:
        why = "would clip the page"
    elif _same_picture(gray, g2) < 0.995:
        why = "would change the picture"
    elif after > before * settings.enhance_max_gain:
        why = "too much (halos)"
    if why:
        info["reason"] = why
        info["sharpness_after"] = round(after, 1)
        return gray, colour, info
    info.update(applied=True, amount=round(amount, 2), sigma=sigma, sharpness_after=round(after, 1))
    return g2, _unsharp(colour, sigma, amount), info
