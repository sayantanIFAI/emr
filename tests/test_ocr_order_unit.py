"""Unit test for OCR reading-order sorting. No infra, no OCR engine needed."""
from __future__ import annotations

from cdi_adapter.ocr.rapid import OcrLine, order_reading


def _ln(x0, y0, x1, y1, text):  # noqa: ANN001
    return OcrLine(text=text, bbox=[x0, y0, x1, y1], conf=0.9, polygon=[])


def test_order_reading_top_to_bottom_left_to_right():
    lines = [
        _ln(400, 500, 600, 530, "row2-right"),
        _ln(40, 20, 200, 48, "title-left"),
        _ln(40, 500, 220, 530, "row2-left"),
        _ln(260, 22, 500, 50, "title-right"),
    ]
    out = [ln.text for ln in order_reading(lines)]
    assert out == ["title-left", "title-right", "row2-left", "row2-right"]


def test_order_reading_empty():
    assert order_reading([]) == []


def test_order_reading_single():
    x = [_ln(0, 0, 10, 10, "only")]
    assert order_reading(x)[0].text == "only"
