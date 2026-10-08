"""Pen marks on a pre-printed checklist (extract/marks.py + the scan): a mark by a printed test name means it was ordered."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.extract import lab_mapping, marks
from cdi_adapter.extract import test_cluster as T

BLUE = (170, 60, 30)        # BGR: the pad's blue print
PEN = (40, 40, 40)          # dark grey pen


def page(lines, strokes=(), w=700, h=420):
    """A paper-coloured page with blue 'printed' lines of text and pen strokes; returns (image, blocks)."""
    img = np.full((h, w, 3), (200, 205, 205), np.uint8)
    blocks = []
    for i, text in enumerate(lines):
        y = 60 + i * 60
        cv2.putText(img, text, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, BLUE, 2, cv2.LINE_AA)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        blocks.append({"text": text, "bbox": [40, y - th - 4, 40 + tw, y + 8], "recognition": {"state": "printed"}})
    for kind, args in strokes:
        if kind == "circle":
            cv2.ellipse(img, args[0], args[1], 0, 0, 360, PEN, 3, cv2.LINE_AA)
        elif kind == "tick":
            cv2.polylines(img, [np.array(args, np.int32)], False, PEN, 4, cv2.LINE_AA)
    return img, blocks


LINES = ["FPG, CREATININE, LIPID PROFILE", "Free T4, TSH", "URINE RE, ACR"]


def test_the_pen_mask_keeps_dark_strokes_and_drops_the_blue_print():
    img, _ = page(LINES, [("circle", ((120, 200), (60, 30)))])
    pen = marks.pen_mask(img)
    assert pen[165:235, 55:185].any()                                   # the circle is there
    assert not pen[40:70, 40:300].any()                                 # the blue print and its halo are not


def test_a_printed_blue_line_is_one_the_reader_called_printed_and_whose_ink_is_blue():
    img, blocks = page(LINES)
    assert all(marks.printed_blue_line(img, b) for b in blocks)
    handwritten = dict(blocks[0], recognition={"state": "single_engine"})
    assert not marks.printed_blue_line(img, handwritten)                 # the reader's label alone decides nothing, but it is needed
    black = img.copy()
    black[...] = 200
    cv2.putText(black, LINES[0], (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2, cv2.LINE_AA)
    assert not marks.printed_blue_line(black, blocks[0])                 # black ink is not this pad's print


def test_a_pen_tick_before_a_printed_name_marks_that_line_and_the_unmarked_printed_names_are_dropped():
    img, blocks = page(LINES, [("tick", [(15, 58), (24, 70), (36, 40)])])                 # a tick at the start of line 1 (FPG)
    got = {f.test: f.why for f in T.scan(blocks, img)}
    assert got.get("FPG") == "marked" or "Fasting" in str(got) or any(w == "marked" for w in got.values())
    assert "TSH" not in got and "ACR" not in got                                          # printed, no mark by them: not ordered


def test_a_page_with_no_pen_mark_is_left_exactly_as_it_was():
    img, blocks = page(LINES)
    assert [f.test for f in T.scan(blocks, img)] == [f.test for f in T.scan(blocks)]


def test_without_a_colour_page_or_with_a_wrong_sized_one_nothing_changes():
    img, blocks = page(LINES, [("tick", [(15, 58), (24, 70), (36, 40)])])
    assert [f.test for f in T.scan(blocks, None)] == [f.test for f in T.scan(blocks)]
    assert T.scan([], img) == [] and T.scan(None, img) == []


@pytest.mark.parametrize("name,standard", [
    ("FPG", "Fasting blood sugar"), ("2hr PPG", "Post-prandial blood sugar"), ("PPG", "Post-prandial blood sugar"),
    ("ACR", "Urine albumin/creatinine ratio"), ("LIPIDPROFILE", "Lipid profile"), ("FreeT4", "Free T4"),
])
def test_printed_checklist_names_and_names_the_reader_glued_together_are_in_the_lists(name, standard):
    assert lab_mapping.lookup(name).canonical == standard


def test_pen_marks_are_off_by_default_until_they_are_measured_on_labelled_pages():
    from cdi_adapter.config import settings
    assert settings.marks_enabled is False

