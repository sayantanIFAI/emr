"""Page geometry for IM-S2: turn a page upright, cut it out of a photo, and remember exactly how.

Every step is a 3x3 matrix in pixel coordinates (x right, y down, pixel centres at integers). The
product of the steps is stored with the page (``preproc.transform``), so a box found on the
straightened copy can be mapped back to the pixels of the original render
(:func:`map_box_to_original`): evidence stays true. The original upload is never touched.

Rules (nothing is guessed):

* a sideways page is turned only when which way is up can be decided; otherwise the page is left
  alone and held for a retake (``orientation_uncertain``);
* a page seen at an angle is cut out and flattened only when four clear corners are found; a photo
  with a background but no found edges is read as it is and flagged "needs check"
  (``page_edges_not_found`` warning, never a hold: the picture is still readable); a flat scan, or a
  page that fills the frame, is left alone;
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


def _thin_ink(small: np.ndarray) -> np.ndarray:
    """Thin dark strokes (handwriting, print, rules) as a boolean map. Big dark areas (a coloured
    bedspread, a shadow) do not show up in it, which is what lets it tell text from background."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(small, cv2.MORPH_BLACKHAT, k) > 30


def _cuts_through_text(ink: np.ndarray, quad: np.ndarray) -> bool:
    """True if any side of ``quad`` slices through writing: ink continues just outside that side about
    as densely as just inside it. A page edge has margin or background beyond it, never more text."""
    h, w = ink.shape
    band = max(6, int(0.025 * max(h, w)))
    centre = quad.mean(axis=0)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        edge = b - a
        length = float(np.hypot(*edge))
        if length < 1:
            return True
        n = np.array([edge[1], -edge[0]]) / length
        if float(np.dot(n, a - centre)) < 0:
            n = -n                                     # points away from the page
        dens = []
        for sign in (1.0, -1.0):
            poly = np.array([a, b, b + sign * n * band, a + sign * n * band], np.int32)
            m = np.zeros((h, w), np.uint8)
            cv2.fillPoly(m, [poly], 1)
            full = int(cv2.countNonZero(m))
            m[ink == 0] = 0
            seen = int(cv2.countNonZero(m))
            area = float(cv2.contourArea(poly.astype(np.float32)))
            dens.append((seen / max(1.0, area), full / max(1.0, area)))
        (d_out, vis_out), (d_in, _vis_in) = dens
        if vis_out < 0.3:
            continue                                   # beyond the picture's edge: nothing to judge
        if d_out >= max(0.012, 0.5 * d_in):
            return True
    return False


def find_page_quad_loose(gray: np.ndarray) -> np.ndarray | None:
    """Second try for a page the strict search missed (a bright page on a busy, partly bright
    background such as patterned bedding): thresholds from bright to dim, each region fitted with
    four corners. The first (tightest) fit that covers a good part of the picture, carries writing
    and does NOT cut through any of it is the page. Nothing is guessed: ``None`` when none fits."""
    h, w = gray.shape[:2]
    if min(h, w) < settings.perspective_min_side_px:
        return None
    s = 1000.0 / max(h, w)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    blur = cv2.GaussianBlur(small, (7, 7), 0)
    ink = _thin_ink(small)
    total = small.shape[0] * small.shape[1]
    for t in range(230, 85, -10):
        mask = cv2.threshold(blur, t, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts, _h = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        area = cv2.contourArea(c)
        if area < settings.perspective_min_area_frac * total:
            continue
        _bx, _by, bw, bh = cv2.boundingRect(c)
        if bw >= 0.98 * small.shape[1] and bh >= 0.98 * small.shape[0]:
            return None                # the region has swallowed the whole picture: lower thresholds only do worse
        hull = cv2.convexHull(c)
        if area / max(1.0, cv2.contourArea(hull)) < 0.85:
            continue                   # not a solid page-shaped region
        peri = cv2.arcLength(hull, True)
        quad = None
        for eps in (0.015, 0.02, 0.03, 0.04):
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4:
                quad = approx.reshape(4, 2).astype(float)
                break
        if quad is None:
            continue
        inside = np.zeros(small.shape[:2], np.uint8)
        cv2.fillConvexPoly(inside, quad.astype(np.int32), 255)
        if float(ink[inside > 0].mean()) < 0.002:
            continue                   # a bright shape with no writing on it is not a page
        if _cuts_through_text(ink, quad):
            continue                   # the page goes on past this edge: a brighter patch, not the page
        return _order(quad / s if s < 1 else quad)
    return None


def find_content_box(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    """[x0, y0, x1, y1) of the writing on the page, plus a margin, when the page's exact corners cannot
    be found: the picture is cropped to it so the background around the page is not read. Built from
    thin strokes only (text, rules), joined into the largest connected block, so it never cuts
    through writing. ``None`` when that would crop almost nothing, or finds no block of writing."""
    h, w = gray.shape[:2]
    s = 1000.0 / max(h, w)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    ink = _thin_ink(small).astype(np.uint8)
    k = max(9, int(0.04 * max(small.shape)))
    joined = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, _c = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n < 2:
        return None
    best = 1 + int(np.argmax([int(ink[lab == i].sum()) for i in range(1, n)]))
    x, y, bw, bh = (int(v) for v in stats[best, :4])
    pad = int(0.08 * max(small.shape))      # generous: coloured logos and faint print are not "thin ink"
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(small.shape[1], x + bw + pad), min(small.shape[0], y + bh + pad)
    frac = (x1 - x0) * (y1 - y0) / float(small.shape[0] * small.shape[1])
    if frac < 0.15 or frac > 0.92:
        return None
    r = 1.0 / s if s < 1 else 1.0
    return (int(x0 * r), int(y0 * r), min(w, int(np.ceil(x1 * r))), min(h, int(np.ceil(y1 * r))))


def page_fills_frame(gray: np.ndarray) -> bool:
    """True when the page itself covers (nearly) the whole picture, so there is no background to cut
    away: one convex four-cornered bright region spanning the frame, with writing on it."""
    h, w = gray.shape[:2]
    s = 1000.0 / max(h, w)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    blur = cv2.GaussianBlur(small, (7, 7), 0)
    mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _h = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return False
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.85 * small.shape[0] * small.shape[1]:
        return False
    approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return False
    inside = np.zeros(small.shape[:2], np.uint8)
    cv2.fillConvexPoly(inside, approx.reshape(4, 2), 255)
    return float(_thin_ink(small)[inside > 0].mean()) >= 0.002


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
