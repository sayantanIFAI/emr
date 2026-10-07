"""The page is cut out of a photo by its colour before anything reads it (the floor / bedspread is never read).

Synthetic photos only: a white page laid on a red cloth or a speckled grey floor, running off one edge of the frame so no
four clean corners exist. The thresholds are measured on five real photos (see docs), not on these.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.ingest import geometry as G
from cdi_adapter.ingest import pages


def _photo(floor: str, seed: int = 1) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    rng = np.random.default_rng(seed)
    h, w = 1000, 800
    if floor == "cloth":
        bg = np.zeros((h, w, 3), np.uint8)
        bg[:] = (40, 40, 170)                                           # red cloth (BGR)
        bg = np.clip(bg.astype(int) + rng.integers(-12, 12, (h, w, 1)), 0, 255).astype(np.uint8)
    else:
        g = rng.integers(30, 170, (h, w), dtype=np.uint8)               # speckled grey stone (darker than the page)
        bg = cv2.cvtColor(cv2.GaussianBlur(g, (0, 0), 2), cv2.COLOR_GRAY2BGR)
    img = bg.copy()
    x0, y0, x1, y1 = 120, 60, 800, 1000                                 # the page runs off the right and bottom edges
    img[y0:y1, x0:x1] = (246, 244, 242)
    cv2.putText(img, "To review after 2 wks {HbA1c / FBS}", (140, 950), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)
    cv2.putText(img, "PPBS", (140, 500), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 3)
    cv2.putText(img, "LFT", (700, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 3)
    return img, (x0, y0, x1, y1)


@pytest.mark.parametrize("floor", ["cloth", "stone"])
def test_the_floor_is_painted_white_and_the_page_is_cropped(floor):
    img, (px0, py0, px1, py1) = _photo(floor)
    got = G.page_cutout(img)
    assert got is not None
    out, (x0, y0, x1, y1), info = got
    assert 0 < info["paper_fraction"] < 0.97
    assert abs(x0 - px0) <= 25 and abs(y0 - py0) <= 25                  # cropped to the page (a few px of margin)
    assert out.shape[:2] == (y1 - y0, x1 - x0)
    # (almost) none of the floor survives in the cropped picture: the strip of rows along the top edge is page or white
    top = out[:max(3, int(0.01 * out.shape[0])), :]
    assert (top.min(axis=2) < 150).mean() < 0.05


@pytest.mark.parametrize("floor", ["cloth", "stone"])
def test_writing_at_the_bottom_left_and_right_of_the_page_is_kept(floor):
    img, (px0, py0, _, _) = _photo(floor)
    out, (x0, y0, _, _), _ = G.page_cutout(img)
    for (x, y) in [(150, 945), (160, 495), (710, 595)]:                 # the three pieces of writing
        patch = out[y - y0 - 20:y - y0 + 10, x - x0 - 5:x - x0 + 60]
        assert patch.min() < 90, "the writing was removed"


def test_the_pages_own_pixels_are_unchanged():
    img, (px0, py0, px1, py1) = _photo("cloth")
    out, (x0, y0, x1, y1), _ = G.page_cutout(img)
    inner = img[py0 + 40:py1 - 40, px0 + 40:px1 - 40]
    got = out[py0 + 40 - y0:py1 - 40 - y0, px0 + 40 - x0:px1 - 40 - x0]
    assert np.array_equal(inner, got)


def test_a_page_that_fills_the_frame_is_not_cut():
    img = np.full((900, 700, 3), (245, 243, 241), np.uint8)
    cv2.putText(img, "Tab Metformin 500 mg", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 2)
    assert G.page_cutout(img) is None


def test_ingest_records_the_crop_in_the_page_transform():
    img, _ = _photo("cloth")
    ok, enc = cv2.imencode(".png", img)
    norm, src, meta = pages.normalize_with_source(enc.tobytes())
    assert "crop_to_page" in meta["steps"] or "perspective" in meta["steps"]
