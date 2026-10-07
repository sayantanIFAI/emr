"""Enlarging and sharpening a soft photo before it is read (ingest/enhance.py). Synthetic pictures only; thresholds are placeholders."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.config import settings
from cdi_adapter.ingest import enhance as E
from cdi_adapter.ingest import pages


@pytest.fixture(autouse=True)
def _enhancement_on(monkeypatch):
    """The feature is OFF by default (it made a real photo's patient name worse); these tests are about the feature itself."""
    monkeypatch.setattr(settings, "enhance_enabled", True)


def _page(w=700, h=900, blur=0.0) -> np.ndarray:
    img = np.full((h, w, 3), 245, np.uint8)
    for i, y in enumerate(range(80, h - 80, 70)):
        cv2.putText(img, "HbA1c  FBS  PPBS  TSH %d" % i, (50, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (25, 25, 25), 2, cv2.LINE_AA)
    return cv2.GaussianBlur(img, (0, 0), blur) if blur else img


def test_a_small_picture_is_enlarged_towards_the_target_and_a_big_one_is_not():
    assert E.upscale_factor(900, 700) == pytest.approx(min(settings.enhance_max_scale, settings.enhance_target_long_side / 900))
    assert E.upscale_factor(2400, 1700) == 1.0
    assert E.upscale(_page(), 2.0).shape[:2] == (1800, 1400)


def test_a_soft_picture_is_sharpened_and_measurably_sharper():
    soft = cv2.cvtColor(E.upscale(_page(blur=2.2), 2.0), cv2.COLOR_BGR2GRAY)
    g, c, info = E.sharpen(soft, cv2.cvtColor(soft, cv2.COLOR_GRAY2BGR))
    assert info["applied"] and info["sharpness_after"] > info["sharpness_before"] * 1.05
    assert g.shape == soft.shape and c.shape[:2] == soft.shape


def test_a_crisp_picture_is_left_alone():
    img = np.full((900, 700), 245, np.uint8)
    for y in range(80, 820, 40):                                                 # hard-edged print: a sharp scan
        cv2.putText(img, "HbA1c FBS PPBS TSH LFT KFT CBC", (40, y), cv2.FONT_HERSHEY_PLAIN, 1.6, 20, 2, cv2.LINE_8)
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    assert E.sharpness(big) >= settings.enhance_crisp_above, E.sharpness(big)
    g, _c, info = E.sharpen(big, cv2.cvtColor(big, cv2.COLOR_GRAY2BGR))
    assert not info["applied"] and np.array_equal(g, big)


def test_a_result_that_fails_the_checks_is_not_used(monkeypatch):
    soft = cv2.cvtColor(E.upscale(_page(blur=2.2), 2.0), cv2.COLOR_BGR2GRAY)
    monkeypatch.setattr(settings, "enhance_max_gain", 1.0)                         # any gain counts as halos
    g, _c, info = E.sharpen(soft, cv2.cvtColor(soft, cv2.COLOR_GRAY2BGR))
    assert not info["applied"] and np.array_equal(g, soft) and info["reason"]


def test_switched_off_changes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "enhance_enabled", False)
    ok, png = cv2.imencode(".png", _page(blur=2.0))
    _n, _s, meta = pages.normalize_with_source(png.tobytes())
    assert "upscale" not in meta["steps"] and "sharpen" not in meta["steps"]


def test_ingest_enlarges_and_records_it_in_the_page_transform():
    ok, png = cv2.imencode(".png", _page(blur=2.0))
    norm, src, meta = pages.normalize_with_source(png.tobytes())
    assert "upscale" in meta["steps"] and meta["width_px"] > 700
    ops = [s["op"] for s in meta["transform"]["steps"]]
    assert "upscale" in ops
    out = cv2.imdecode(np.frombuffer(src, np.uint8), cv2.IMREAD_COLOR)
    assert out.shape[1] == meta["width_px"]                                      # the colour render lines up with the page
