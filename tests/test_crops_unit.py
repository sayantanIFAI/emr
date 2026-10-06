"""Crop standard (IM-S3): every handwriting crop meets it and its size is recorded. No database."""
from __future__ import annotations

import hashlib
import io

import cv2
import numpy as np
import pytest
from PIL import Image

from cdi_adapter.config import settings
from cdi_adapter.recognition.regions import prepare_crop


def _page(w=1700, h=2200) -> np.ndarray:
    rng = np.random.default_rng(5)
    return np.clip(rng.normal(235, 6, (h, w, 3)), 0, 255).astype(np.uint8)


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


@pytest.mark.parametrize("height", [14, 20, 28, 32, 40, 60, 120])
def test_ac4_lines_of_different_heights_all_meet_the_standard_and_their_size_is_recorded(height):
    page = _page()
    bbox = [200, 500, 1400, 500 + height]
    png, info = prepare_crop(page, bbox)
    w, h = _size(png)
    assert h >= settings.crop_min_height_px and info["below_standard"] is False
    assert info["out_wh"] == [w, h] and info["bbox"] == bbox
    assert info["cut_wh"][1] >= height                                  # padding was added, nothing was cut away


def test_a_tall_enough_line_is_not_resampled():
    page = _page()
    png, info = prepare_crop(page, [200, 500, 1400, 580])
    assert info["upscaled"] is False and info["scale"] == 1.0
    assert _size(png) == tuple(info["cut_wh"])
    cut = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    x0, y0 = 200 - info["pad"][0], 500 - info["pad"][1]
    assert np.array_equal(cut, page[y0:y0 + cut.shape[0], x0:x0 + cut.shape[1]])      # pixel-exact, untouched


def test_a_small_crop_is_enlarged_with_its_aspect_ratio_kept():
    page = _page()
    _png, info = prepare_crop(page, [200, 500, 1400, 512])               # 12 px line
    assert info["upscaled"] is True and 1.0 < info["scale"] <= settings.crop_max_upscale
    cw, ch = info["cut_wh"]
    ow, oh = info["out_wh"]
    assert oh >= settings.crop_min_height_px and abs(ow / oh - cw / ch) < 0.02


def test_a_crop_too_small_even_for_the_largest_enlargement_is_flagged_below_standard(monkeypatch):
    monkeypatch.setattr(settings, "crop_max_upscale", 2.0)
    page = _page()
    png, info = prepare_crop(page, [200, 500, 1400, 504])               # 4 px line + 8 px padding = 12 px
    assert info["scale"] == 2.0 and info["below_standard"] is True
    assert _size(png)[1] == 24 < settings.crop_min_height_px            # enlarged as far as allowed, no further


def test_padding_is_applied_on_every_side_and_never_goes_outside_the_page():
    page = _page(800, 600)
    _png, inside = prepare_crop(page, [300, 200, 500, 240])
    assert all(p >= settings.crop_pad_min_px for p in inside["pad"])
    _png, edge = prepare_crop(page, [0, 0, 100, 40])
    assert edge["pad"][0] == 0 and edge["pad"][1] == 0 and edge["pad"][2] > 0 and edge["pad"][3] > 0
    _png, corner = prepare_crop(page, [700, 560, 800, 600])
    assert corner["cut_wh"][0] <= 800 and corner["pad"][2] == 0 and corner["pad"][3] == 0


def test_the_padding_follows_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "crop_pad_frac", 0.1)
    monkeypatch.setattr(settings, "crop_pad_min_px", 10)
    _png, info = prepare_crop(_page(), [300, 500, 1100, 580])
    assert info["pad"][0] == 80 and info["pad"][1] == 10


def test_upscaling_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "crop_min_height_px", 0)
    png, info = prepare_crop(_page(), [200, 500, 1400, 512])
    assert info["upscaled"] is False and info["below_standard"] is False and _size(png) == tuple(info["cut_wh"])


def test_the_same_crop_always_gives_the_same_bytes_so_its_hash_is_stable():
    page = _page()
    a, ia = prepare_crop(page, [200, 500, 1400, 512])
    b, ib = prepare_crop(page, [200, 500, 1400, 512])
    assert a == b and ia == ib and hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_the_record_is_plain_json_data():
    import json

    _png, info = prepare_crop(_page(), [200, 500, 1400, 512])
    json.dumps(info)                                                    # it goes into the ocr_block record
    assert set(info) == {"bbox", "pad", "cut_wh", "scale", "out_wh", "upscaled", "below_standard"}
