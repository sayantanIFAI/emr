"""Tests are written together: the page's own text is searched by position, with no model (extract/test_cluster.py)."""
from __future__ import annotations

import pytest

from cdi_adapter.extract import test_cluster as T


def B(text, x0, y0, x1, y1):
    return {"text": text, "bbox": [x0, y0, x1, y1]}


# the lines and boxes of a real prescription (565 x 956 px), as the readers produced them
DEBABRATA = [B("MR. Debabrata San?ar(63/M) DATE 16/03/24", 3, 190, 546, 231), B("STOP SMOKING", 325, 312, 503, 340),
             B("Tabs. Glimepiride 10?/Generic", 132, 541, 483, 586), B("Tab. Aliv?ng(20) 1tros afm Dmndly Cap. Vit?ria", 149, 712, 506, 785),
             B("x cap. lid?niz Ds(bow)/DVRK.", 3, 751, 515, 956), B("fas, fas", 9, 771, 97, 804), B("100mg/5mL", 3, 799, 82, 819),
             B("vitam?? D", 3, 804, 112, 837), B("l?p", 281, 814, 332, 842), B("pn/kx2oh", 346, 849, 511, 875), B("1m??", 384, 876, 486, 928)]


def test_the_real_page_gives_the_fasting_sugar_slip_and_vitamin_d_beside_it():
    got = [(f.test, f.why) for f in T.scan(DEBABRATA)]
    assert ("FBS", "near") in got and ("Vitamin d", "beside") in got
    assert not any(t in ("Tabs. Glimepiride", "STOP SMOKING") for t, _ in got)          # medicines and advice are not tests


def test_vitamin_d_on_its_own_or_among_medicines_is_not_taken_but_beside_tests_it_is():
    assert T.scan([B("vitamin D", 3, 804, 112, 837)]) == []
    assert T.scan([B("Tab Metformin 500mg", 3, 100, 300, 130), B("vitamin D", 3, 135, 110, 165)]) == []
    assert [f.test for f in T.scan([B("CBC", 3, 100, 60, 130), B("vitamin D", 3, 135, 110, 165)])] == ["CBC", "vitamin D"]
    assert [f.test for f in T.scan([B("CBC", 3, 100, 60, 130), B("vitamin D", 300, 600, 410, 630)])] == ["CBC"]    # far away: not beside


def test_an_ambiguous_name_inside_a_sentence_is_never_a_list_entry():
    got = T.scan([B("CBC", 3, 100, 60, 130), B("calcium rich diet and exercise daily here", 3, 135, 400, 165)])
    assert [f.test for f in got] == ["CBC"]


@pytest.mark.parametrize("line", ["25(OH) vit D", "25 OH Vit D", "pn/kx2oh"])
def test_the_25_oh_mark_alone_is_the_vitamin_d_test(line):
    assert any(f.test.casefold().startswith("vit") for f in T.scan([B(line, 3, 100, 200, 130)]))


def test_strong_names_are_tests_wherever_they_are_and_a_list_is_taken_whole():
    got = [f.test for f in T.scan([B("CBC, LFT, RFT", 3, 100, 200, 130), B("FBS PPBS", 3, 135, 150, 165), B("25 OH Vit D", 3, 170, 200, 200)])]
    assert got == ["CBC", "LFT", "RFT", "FBS", "PPBS", "Vit D"]
    assert [f.test for f in T.scan([B("TSH", 400, 900, 450, 930)])] == ["TSH"]


@pytest.mark.parametrize("word,expected", [("fas", "FBS"), ("lfl", "LFT"), ("far", None), ("for", None), ("cbc", None), ("tsh", None), ("ab", None)])
def test_only_a_one_letter_handwriting_slip_of_one_abbreviation_counts(word, expected):
    assert T.near_miss(word) == expected


def test_a_slip_on_its_own_is_not_taken_but_two_in_a_list_or_one_beside_a_test_are():
    assert T.scan([B("fas", 3, 100, 60, 130)]) == []
    assert [f.test for f in T.scan([B("fas, fas", 3, 100, 100, 130)])] == ["FBS"]
    assert [f.test for f in T.scan([B("CBC", 3, 100, 60, 130), B("fas", 3, 135, 60, 165)])] == ["CBC", "FBS"]


def test_where_a_test_came_from_is_said_in_words():
    notes = {f.test: f.note for f in T.scan(DEBABRATA)}
    assert "one letter from FBS" in notes["FBS"] and "beside other tests" in notes["Vitamin d"]


def test_lines_without_boxes_are_grouped_in_reading_order():
    got = [f.test for f in T.scan([{"text": "CBC"}, {"text": "vitamin D"}])]
    assert got == ["CBC", "vitamin D"]


def test_pt_is_the_test_only_beside_tests_never_as_the_word_patient():
    assert T.scan([B("Currently moderate articolar activity as conveyed by pt henc", 196, 560, 672, 692)]) == []     # MEASURED: a real page
    assert [f.test for f in T.scan([B("pt", 3, 100, 40, 130)])] == []
    assert [f.test for f in T.scan([B("CBC", 3, 100, 60, 130), B("PT", 3, 135, 40, 165)])] == ["CBC", "PT"]


def test_a_word_that_can_be_read_two_ways_is_not_taken():
    assert T.near_miss("apt") is None and T.near_miss("fas") == "FBS"                # apt: AST by one letter or APTT by a doubled letter


def test_printed_text_is_the_clinics_not_an_order():
    printed = {"text": "Endoscopy Ultrasonography ECG Echocardiography", "bbox": [138, 1491, 1123, 1511], "recognition": {"state": "printed"}}
    assert T.scan([printed]) == []
    hand = dict(printed, recognition={"state": "single_engine"})
    assert [f.test for f in T.scan([hand])] == ["ECG"]

