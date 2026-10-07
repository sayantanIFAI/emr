"""Regions that are not writing on paper (fabric, table, hand, stray marks) are set aside, never read."""
from __future__ import annotations

import cv2
import numpy as np

from cdi_adapter.recognition import nontext
from cdi_adapter.recognition.regions import HANDWRITTEN, PRINTED, Region


def _page() -> np.ndarray:
    img = np.full((600, 800, 3), (235, 225, 220), np.uint8)               # bluish-white paper (BGR)
    img[:, 620:] = (40, 120, 230)                                         # an orange bedspread on the right
    img[:60, :] = (30, 150, 60)                                           # a green strip on top
    cv2.putText(img, "Tab Telmisartan 40 1-0-1", (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (60, 30, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "Cap Omeprazole 20 mg", (30, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (60, 30, 20), 2, cv2.LINE_AA)
    return img


def _r(kind, box, **feat):
    return Region(kind, box, [], dict(feat))


def test_writing_on_paper_is_kept_and_fabric_blank_paper_and_specks_are_set_aside():
    regs = [_r(HANDWRITTEN, [25, 170, 500, 215]),          # writing on paper
            _r(HANDWRITTEN, [630, 100, 790, 400]),         # the bedspread
            _r(HANDWRITTEN, [30, 90, 400, 130]),           # empty paper
            _r(HANDWRITTEN, [50, 500, 58, 506]),           # a speck
            _r(HANDWRITTEN, [20, 5, 600, 55])]             # the green strip
    out = nontext.classify_regions(regs, _page())
    assert [r.kind for r in regs] == [HANDWRITTEN, nontext.NON_TEXT, nontext.NON_TEXT, nontext.NON_TEXT, nontext.NON_TEXT]
    assert out == {"non_text": 4, "regions": 5}
    assert regs[1].features["was_kind"] == HANDWRITTEN and regs[1].features["non_text_reason"]
    assert regs[1].features["paper_distance"] > 40 and regs[4].features["paper_distance"] > 40      # fabric is far from paper
    assert "thin pen" in regs[2].features["non_text_reason"] and "too small" in regs[3].features["non_text_reason"]


def test_the_measurements_are_kept_on_every_region_even_the_kept_ones():
    regs = [_r(HANDWRITTEN, [25, 170, 500, 215])]
    nontext.classify_regions(regs, _page())
    assert regs[0].features["thin_ink_frac"] > 0.03 and regs[0].features["paper_distance"] < 10


def test_a_confidently_read_printed_line_is_trusted_even_on_a_coloured_background():
    img = _page()
    cv2.putText(img, "SOME LOGO", (640, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    sure = _r(PRINTED, [635, 170, 790, 215], ocr_mean_conf=0.95, ocr_coverage=0.9)
    unsure = _r(HANDWRITTEN, [635, 170, 790, 215])
    nontext.classify_regions([sure, unsure], img)
    assert sure.kind == PRINTED and unsure.kind == nontext.NON_TEXT


def test_a_shadow_across_half_the_page_does_not_make_it_non_paper():
    img = _page()
    img[:, :400] = (img[:, :400] * 0.55).astype(np.uint8)                  # darker, same colour
    cv2.putText(img, "Cap Omeprazole 20 mg", (30, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 10, 5), 2, cv2.LINE_AA)
    regs = [_r(HANDWRITTEN, [25, 270, 380, 315])]
    nontext.classify_regions(regs, img)
    assert regs[0].kind == HANDWRITTEN


def test_the_pipeline_skips_non_text_regions_entirely():
    from cdi_adapter.recognition import pipeline
    assert pipeline.nontext.NON_TEXT == "non_text"
