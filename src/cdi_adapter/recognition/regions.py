"""Line / region detection (E2-S13) - OpenCV, CPU.

Finds text lines on the normalised page, then labels each one printed | handwritten |
mixed | uncertain from evidence:

* RapidOCR coverage + confidence over the line (printed text OCRs cleanly),
* stroke-width variation (a printed font has near-uniform strokes; a pen does not).

'uncertain' lines go to BOTH paths - they are never dropped. Crops are cut from the
pixel-faithful source render (``document_page.preproc.src_uri``), not the filtered image.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..ocr.rapid import OcrLine

PRINTED, HANDWRITTEN, MIXED, UNCERTAIN = "printed", "handwritten", "mixed", "uncertain"


@dataclass
class Region:
    kind: str
    bbox: list[int]                                   # [x0, y0, x1, y1] page px
    ocr_lines: list[OcrLine] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_handwriting_engines(self) -> bool:
        return self.kind in (HANDWRITTEN, MIXED, UNCERTAIN)


def _binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 31, 15)


def _remove_rules(ink: np.ndarray) -> np.ndarray:
    """Drop long ruled lines / table borders so they do not fuse text lines together."""
    h, w = ink.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 8), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 8)))
    rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk)
    return cv2.subtract(ink, rules)


def detect_lines(gray: np.ndarray) -> list[list[int]]:
    """Text-line boxes from ink blobs merged along the reading direction."""
    h, w = gray.shape[:2]
    ink = _remove_rules(_binarize(gray))
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))   # speckle
    kx = max(15, w // 45)
    merged = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 3)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(merged, connectivity=8)
    boxes: list[list[int]] = []
    min_h, max_h = max(6, h // 250), h // 4
    for i in range(1, n):
        x, y, bw, bh, area = (int(stats[i, k]) for k in range(5))
        if bh < min_h or bh > max_h or bw < max(12, w // 100) or area < 60:
            continue
        # shrink the dilation margin back off the box
        pad = kx // 2
        boxes.append([max(0, x + pad // 2), y, min(w, x + bw - pad // 2), y + bh])
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def stroke_width_cv(gray_crop: np.ndarray) -> float | None:
    """Coefficient of variation of stroke width (distance transform on ink skeleton)."""
    ink = _binarize(gray_crop)
    if (ink > 0).sum() < 30:
        return None
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
    vals = dist[dist > 0]
    # sample the stroke centres (local maxima approximated by the upper half of distances)
    centres = vals[vals >= np.median(vals)]
    if centres.size < 10 or centres.mean() <= 0:
        return None
    return float(centres.std() / centres.mean())


def _overlap(a: list[int], b: list[int]) -> float:
    """Share of box ``a`` covered by box ``b``."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def classify_line(bbox: list[int], ocr: list[OcrLine], swcv: float | None) -> tuple[str, dict]:
    hits = [ln for ln in ocr if _overlap(ln.bbox, bbox) >= 0.5 or _overlap(bbox, ln.bbox) >= 0.5]
    width = max(1, bbox[2] - bbox[0])
    covered = sum(max(0, min(ln.bbox[2], bbox[2]) - max(ln.bbox[0], bbox[0])) for ln in hits)
    cov = min(1.0, covered / width)
    mconf = float(np.mean([ln.conf for ln in hits])) if hits else 0.0
    feats = {"ocr_coverage": round(cov, 3), "ocr_mean_conf": round(mconf, 3),
             "stroke_width_cv": round(swcv, 3) if swcv is not None else None,
             "ocr_hits": len(hits)}
    # The strong signal is RapidOCR itself: printed text OCRs cleanly, handwriting does not.
    # Stroke-width variation is weak on its own (measured: printed ~0.33) and is only used to
    # catch a confidently-OCR'd line whose strokes look hand-drawn (-> mixed, both paths).
    irregular = swcv is not None and swcv >= 0.42
    if cov >= 0.6 and mconf >= 0.85:
        return (MIXED if irregular else PRINTED), feats
    if cov < 0.3 and (mconf < 0.6 or not hits):
        return HANDWRITTEN, feats
    if 0.3 <= cov < 0.6:
        return MIXED, feats
    return UNCERTAIN, feats


def detect_regions(gray: np.ndarray, ocr_lines: list[OcrLine]) -> list[Region]:
    """Every detected line becomes a Region; RapidOCR lines no detector box explains are
    kept as printed regions so no OCR evidence is lost."""
    regions: list[Region] = []
    used: set[int] = set()
    for b in detect_lines(gray):
        crop = gray[b[1]:b[3], b[0]:b[2]]
        kind, feats = classify_line(b, ocr_lines, stroke_width_cv(crop) if crop.size else None)
        hits = [i for i, ln in enumerate(ocr_lines)
                if _overlap(ln.bbox, b) >= 0.5 or _overlap(b, ln.bbox) >= 0.5]
        used.update(hits)
        regions.append(Region(kind, b, [ocr_lines[i] for i in hits], feats))
    for i, ln in enumerate(ocr_lines):
        if i not in used:
            regions.append(Region(PRINTED, list(ln.bbox), [ln],
                                  {"ocr_coverage": 1.0, "ocr_mean_conf": ln.conf,
                                   "source": "rapidocr-only"}))
    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return regions


def prepare_crop(src: np.ndarray, bbox: list[int]) -> tuple[bytes, dict[str, Any]]:
    """One line crop to the CROP STANDARD (IM-S3), and what was done, for the record.

    Cut from the pixel-faithful source render with padding (so strokes are not clipped, never
    beyond the page), and if the result is shorter than ``crop_min_height_px`` it is enlarged
    (cubic, aspect kept) by at most ``crop_max_upscale`` before a reader sees it. The record lists
    the box, the padding actually applied, the size cut and the size delivered, so that errors can
    be sliced by crop size later. ``below_standard`` = still too short after the largest allowed
    enlargement: nothing more is done, and the line is read as it is."""
    from ..config import settings

    h, w = src.shape[:2]
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    mx = max(settings.crop_pad_min_px, int(bw * settings.crop_pad_frac))
    my = max(settings.crop_pad_min_px, int(bh * settings.crop_pad_frac))
    x0, y0 = max(0, bbox[0] - mx), max(0, bbox[1] - my)
    x1, y1 = min(w, bbox[2] + mx), min(h, bbox[3] + my)
    crop = src[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    scale = 1.0
    if settings.crop_min_height_px and 0 < ch < settings.crop_min_height_px:
        scale = min(float(settings.crop_max_upscale), settings.crop_min_height_px / ch)
        if scale > 1.0:
            crop = cv2.resize(crop, (max(1, round(cw * scale)), max(1, round(ch * scale))),
                              interpolation=cv2.INTER_CUBIC)
        else:
            scale = 1.0
    ok, enc = cv2.imencode(".png", crop)
    if not ok:  # pragma: no cover
        raise RuntimeError("crop encode failed")
    out_h, out_w = crop.shape[:2]
    info = {"bbox": [int(v) for v in bbox], "pad": [int(bbox[0] - x0), int(bbox[1] - y0),
                                                    int(x1 - bbox[2]), int(y1 - bbox[3])],
            "cut_wh": [int(cw), int(ch)], "scale": round(scale, 3), "out_wh": [int(out_w), int(out_h)],
            "upscaled": scale > 1.0,
            "below_standard": bool(settings.crop_min_height_px and out_h < settings.crop_min_height_px)}
    return enc.tobytes(), info


def crop_png(src: np.ndarray, bbox: list[int], margin_frac: float = 0.0,
             min_margin_px: int = 0) -> bytes:
    h, w = src.shape[:2]
    mx = max(min_margin_px, int((bbox[2] - bbox[0]) * margin_frac))
    my = max(min_margin_px, int((bbox[3] - bbox[1]) * margin_frac))
    x0, y0 = max(0, bbox[0] - mx), max(0, bbox[1] - my)
    x1, y1 = min(w, bbox[2] + mx), min(h, bbox[3] + my)
    ok, enc = cv2.imencode(".png", src[y0:y1, x0:x1])
    if not ok:  # pragma: no cover
        raise RuntimeError("crop encode failed")
    return enc.tobytes()
