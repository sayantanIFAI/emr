"""Page geometry for IM-S2: turn a page upright, cut it out of a photo, and remember exactly how.

Every step is a 3x3 matrix in pixel coordinates (x right, y down, pixel centres at integers). The
product of the steps is stored with the page (``preproc.transform``), so a box found on the
straightened copy can be mapped back to the pixels of the original render
(:func:`map_box_to_original`): evidence stays true. The original upload is never touched.

Rules (nothing is guessed):

* a sideways page is turned only when which way is up can be decided; otherwise the page is left
  alone and held for a retake (``orientation_uncertain``);
* a page seen at an angle is cut out and flattened only when four clear corners are found; a photo
  with a background but no found edges is held (``page_edges_not_found``); a flat scan that fills the
  frame is left alone;
* upside-down (180 degrees) is corrected only on a clear signal, and only when enabled.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..config import settings

Matrix = np.ndarray


def identity() -> Matrix:
    return np.eye(3)


def rot90_matrix(w: int, h: int, k: int) -> Matrix:
    """The matrix of ``np.rot90(img, k)`` for an image ``w`` x ``h`` (k = 1: counter-clockwise)."""
    k %= 4
    if k == 1:
        return np.array([[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]], float)
    if k == 2:
        return np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]], float)
    if k == 3:
        return np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]], float)
    return identity()


def affine3(m23: np.ndarray) -> Matrix:
    out = identity()
    out[:2, :] = m23
    return out


def apply(m: Matrix, pts: np.ndarray) -> np.ndarray:
    p = np.asarray(pts, float).reshape(-1, 2)
    q = np.hstack([p, np.ones((len(p), 1))]) @ m.T
    return q[:, :2] / q[:, 2:3]


def map_box_to_original(box: list[int], transform: dict[str, Any]) -> list[int]:
    """A box on the straightened copy -> the box (axis-aligned, inclusive of the corners) that covers it
    in the original render. ``box`` is [x0, y0, x1, y1] with x1 / y1 exclusive."""
    inv = np.linalg.inv(np.asarray(transform["matrix"], float))
    x0, y0, x1, y1 = box
    corners = np.array([[x0, y0], [x1 - 1, y0], [x1 - 1, y1 - 1], [x0, y1 - 1]], float)
    src = apply(inv, corners)
    w, h = transform["src_wh"]
    lo = np.floor(src.min(axis=0)).astype(int)
    hi = np.ceil(src.max(axis=0)).astype(int) + 1
    return [int(max(0, lo[0])), int(max(0, lo[1])), int(min(w, hi[0])), int(min(h, hi[1]))]


def new_transform(w: int, h: int) -> dict[str, Any]:
    return {"matrix": identity().tolist(), "src_wh": [int(w), int(h)], "out_wh": [int(w), int(h)], "steps": []}


def add_step(t: dict[str, Any], step: dict[str, Any], m: Matrix, out_wh: tuple[int, int]) -> None:
    t["matrix"] = (m @ np.asarray(t["matrix"], float)).tolist()
    t["out_wh"] = [int(out_wh[0]), int(out_wh[1])]
    t["steps"].append(step)


# ------------------------------------------------------------------ which way is up


def upright_score(gray: np.ndarray) -> tuple[float | None, int]:
    """Median over text lines of (line middle - ink centroid) / line height, and how many lines.

    Upright Latin text has more ink above the middle of a line (capitals, ascenders) than below
    (descenders). MEASURED on synthetic mixed-case text: upright +0.04..+0.055, upside down
    -0.014..-0.018. ALL-CAPS and digit-only lines are symmetric (+0.03 and +0.02) and cannot be
    told apart: they give no verdict. Real handwriting is NOT measured."""
    from ..recognition.quality import _clean_ink

    ink = cv2.morphologyEx(_clean_ink(gray), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    h, w = gray.shape[:2]
    merged = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 45), 3)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(merged, connectivity=8)
    vals: list[float] = []
    for i in range(1, n):
        x, y, bw, bh = (int(stats[i, k]) for k in range(4))
        if bh < 8 or bw < 60 or bh > h // 6:
            continue
        rows = (ink[y:y + bh, x:x + bw] > 0).sum(axis=1).astype(float)
        if rows.sum() < 50:
            continue
        cy = float((rows * np.arange(bh)).sum() / rows.sum())
        vals.append((bh / 2 - cy) / bh)
    return (float(np.median(vals)) if len(vals) >= 6 else None), len(vals)


def decide_sideways(gray: np.ndarray) -> tuple[int | None, dict[str, Any]]:
    """For a page whose lines run vertically: the ``np.rot90`` k (1 or 3) that makes it read
    upright, or ``None`` when that cannot be decided."""
    s1, n1 = upright_score(np.ascontiguousarray(np.rot90(gray, 1)))
    s3, n3 = upright_score(np.ascontiguousarray(np.rot90(gray, 3)))
    info = {"upright_score_k1": s1, "upright_score_k3": s3, "lines": [n1, n3]}
    if s1 is None or s3 is None:
        return None, info
    if s1 - s3 >= settings.orient_upright_margin:
        return 1, info
    if s3 - s1 >= settings.orient_upright_margin:
        return 3, info
    return None, info


# ------------------------------------------------------------------ the page inside a photo


def photo_like(gray: np.ndarray) -> bool:
    """A picture with a background around the page (a photo), as opposed to a scan that fills the
    frame: the outer frame differs clearly from the middle, or is busy (a desk, fabric, clutter)
    where a scan's margin is a flat colour."""
    h, w = gray.shape[:2]
    b = max(2, int(min(h, w) * 0.03))
    frame = np.concatenate([gray[:b].ravel(), gray[-b:].ravel(), gray[:, :b].ravel(), gray[:, -b:].ravel()])
    centre = gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return (abs(float(np.median(frame)) - float(np.median(centre))) > settings.photo_border_contrast
            or float(np.std(frame)) > settings.photo_border_texture)


def _order(pts: np.ndarray) -> np.ndarray:
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], float)  # tl tr br bl


def find_page_quad(gray: np.ndarray) -> np.ndarray | None:
    """Four ordered corners (tl, tr, br, bl) of the page in a photo, or ``None``. The page must be
    the largest bright region and a convex four-sided shape covering a good part of the picture."""
    h, w = gray.shape[:2]
    if min(h, w) < settings.perspective_min_side_px:
        return None            # too small to be a photographed page
    s = 1000.0 / max(h, w)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    blur = cv2.GaussianBlur(small, (7, 7), 0)
    best, best_area = None, 0.0
    for mask in (cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1],
                 cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]):
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts, _h = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area <= best_area or area < settings.perspective_min_area_frac * small.shape[0] * small.shape[1]:
                continue
            _bx, _by, bw, bh = cv2.boundingRect(c)
            if bw >= 0.98 * small.shape[1] and bh >= 0.98 * small.shape[0]:
                continue           # the whole picture (the desk wrapped round the page), not a page
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                inside = np.zeros(small.shape[:2], np.uint8)
                cv2.fillConvexPoly(inside, approx.reshape(4, 2), 255)
                region = small[inside > 0]
                if float((region < float(np.median(region)) - 50).mean()) < 0.002:
                    continue       # a bright or dark shape with no writing on it is not a page
                best, best_area = approx.reshape(4, 2).astype(float), area
    if best is None:
        return None
    return _order(best / s if s < 1 else best)


def quad_moves_enough(quad: np.ndarray, w: int, h: int) -> bool:
    """True if the corners are clearly away from the picture's corners (not already a full-frame page)."""
    frame = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], float)
    return float(np.max(np.linalg.norm(quad - frame, axis=1))) > settings.perspective_min_move_frac * float(np.hypot(w, h))


def rectify(arr: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, Matrix]:
    """The page cut out of the photo and flattened, and the 3x3 matrix that did it."""
    tl, tr, br, bl = quad
    out_w = round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    out_h = round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    out = cv2.warpPerspective(arr, m, (out_w, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, m.astype(float)
