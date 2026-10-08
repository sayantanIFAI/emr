"""Which way up is the page? The printed text decides (ingest/orient_ocr.py), with a fake OCR host: no model, exact."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.config import settings
from cdi_adapter.ingest import geometry as G
from cdi_adapter.ingest import orient_ocr as O
from cdi_adapter.ingest import pages
from cdi_adapter.ocr.rapid import OcrLine


def _page(w=900, h=1200):
    """A page whose 'letterhead' is a dark block at the TOP-LEFT: upright when that block is at the top-left."""
    a = np.full((h, w, 3), 245, np.uint8)
    a[0:200, 0:500] = (30, 30, 30)
    cv2.putText(a, "Apollo Clinic", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3)
    return a


class FakeHost:
    """Reads good text only when the dark block is at the top-left of the picture it is given (the page is upright)."""
    def __init__(self): self.calls = []

    def rapid(self, png, use_cls=True):
        self.calls.append(use_cls)
        a = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        h, w = a.shape
        corner = a[: h // 8, : w // 3].mean()
        elsewhere = [a[: h // 8, -w // 3:].mean(), a[-h // 8:, : w // 3].mean(), a[-h // 8:, -w // 3:].mean()]
        good = corner < 100 and all(e > 150 for e in elsewhere)
        return [OcrLine("Apollo Clinic Consultant Rheumatologist", [0, 0, 10, 10], 0.95 if good else 0.55, [])
                for _ in range(8 if good else 1)]


@pytest.fixture(autouse=True)
def _vote_on(monkeypatch):
    monkeypatch.setattr(settings, "orient_ocr_check", True)
    monkeypatch.setattr(settings, "perspective_enabled", False)           # these tests are about the turn, not the page cut-out
    from cdi_adapter.recognition import ocrhost_client
    monkeypatch.setattr(ocrhost_client, "get_ocr_host", lambda: FakeHost())


def test_the_score_counts_confident_lines_of_letters_only():
    ln = lambda t, c: OcrLine(t, [0, 0, 1, 1], c, [])                                     # noqa: E731
    assert O.read_score([ln("Apollo Clinic", 0.9)]) == pytest.approx(0.9 * 12)
    assert O.read_score([ln("Apollo Clinic", 0.3), ln("12/03/2026", 0.99), ln("a", 0.99), ln("x1y2z3q4", 0.9)]) == 0   # weak, digits, tiny, junk


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_the_turn_that_restores_the_print_wins_over_all_four(k):
    upright = _page()
    turned = np.ascontiguousarray(np.rot90(upright, -k))                                  # the photo as taken: k quarter turns off
    got, info = O.vote(turned, FakeHost())
    assert got == k and info["decided"]


def test_only_the_two_candidates_are_read_when_the_axis_is_known_and_nothing_decides_without_print():
    host = FakeHost()
    sideways = np.ascontiguousarray(np.rot90(_page(), -3))
    got, info = O.vote(sideways, host, candidates=(1, 3))
    assert got == 3 and len(host.calls) == 2 and set(info["scores"]) == {"1", "3"} and not any(host.calls)   # use_cls is OFF for the vote
    blank = np.full((600, 800, 3), 250, np.uint8)
    assert O.vote(blank, FakeHost())[0] is None


def test_a_wrong_direction_from_the_ink_shape_rule_is_overruled_by_the_print(monkeypatch):
    monkeypatch.setattr(G, "decide_sideways", lambda gray: (1, {"upright_score_k1": 0.03, "upright_score_k3": 0.003}))   # the real page's wrong answer
    monkeypatch.setattr(pages, "_orientation_ratio", lambda gray: 9.0)                    # sideways: the axis rule is right
    sideways = np.ascontiguousarray(np.rot90(_page(), -3))                               # needs k = 3
    ok, enc = cv2.imencode(".png", sideways)
    norm, src, meta = pages.normalize_with_source(enc.tobytes())
    out = cv2.imdecode(np.frombuffer(src, np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = out.shape
    assert out[: h // 8, : w // 3].mean() < 100                                           # the dark letterhead block is at the top-left again
    assert meta["steps"].count("rotate90") == 1 and meta["orientation_vote"]["decided"]


def test_with_no_clear_print_the_ink_shape_rules_answer_stands(monkeypatch):
    monkeypatch.setattr(G, "photo_like", lambda gray: False)                              # a flat scan: the ink-shape rule applies
    monkeypatch.setattr(G, "decide_sideways", lambda gray: (3, {"by": "ink shape"}))
    monkeypatch.setattr(pages, "_orientation_ratio", lambda gray: 9.0)
    from cdi_adapter.ingest import orient_ocr
    monkeypatch.setattr(orient_ocr, "vote", lambda arr, host=None, candidates=(0, 1, 2, 3): (None, {"decided": False}))
    ok, enc = cv2.imencode(".png", np.ascontiguousarray(np.rot90(_page(), -3)))
    _n, _s, meta = pages.normalize_with_source(enc.tobytes())
    assert "rotate90" in meta["steps"]                                                    # as before: the old rule decided
    assert [s for s in meta["transform"]["steps"] if s["op"] == "rotate90"][0]["k"] == 3


def test_a_page_the_ink_rule_wants_to_flip_but_the_print_says_is_upright_is_left_alone(monkeypatch):
    monkeypatch.setattr(G, "upright_score", lambda gray: (0.0001, 30))                    # the rule says upside down
    monkeypatch.setattr(settings, "upside_down_threshold", 0.01)
    monkeypatch.setattr(settings, "orient_upside_down", True)
    monkeypatch.setattr(pages, "_orientation_ratio", lambda gray: 1.0)                    # not sideways
    ok, enc = cv2.imencode(".png", _page())
    _n, _s, meta = pages.normalize_with_source(enc.tobytes())
    assert "rotate180" not in meta["steps"] and meta["orientation_vote"]["best"] == 0
