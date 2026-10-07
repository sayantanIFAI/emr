"""A page photographed on a stone floor: '?' lines, floor regions, medicines among the advice, tests next to the follow-up."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import resolve_llm as R
from cdi_adapter.extract import service as X
from cdi_adapter.extract.test_names import split_tests
from cdi_adapter.recognition import nontext
from cdi_adapter.recognition.engines import clean_line
from cdi_adapter.recognition.regions import HANDWRITTEN, Region
from cdi_adapter.webapp.upload_page import ADMIN_PAGE


# ---- readings that are only punctuation are no reading
@pytest.mark.parametrize("raw,want", [("?", ""), ("?" * 110, ""), ("  ?  ", ""), ("...", ""), ("", ""), (None, ""),
                                      ("S.Lipase-916", "S.Lipase-916"), ("T? JAROVA?CE(25) 00", "T? JAROVA?CE(25) 00"),
                                      ("Test? ? ? ? ? ? ? ? ? ? ?", "Test ?"), ("aaaaaaaaaa", "aaa")])
def test_degenerate_readings_are_cut_and_real_letters_are_kept(raw, want):
    assert clean_line(raw) == want


# ---- the floor around the page is not the page
def _photo():
    img = np.full((800, 600, 3), (190, 150, 140), np.uint8)                       # a bluish page
    rng = np.random.default_rng(1)
    floor = (rng.integers(60, 200, (800, 600, 1)) * np.ones((1, 1, 3))).astype(np.uint8)    # speckled grey stone
    out = floor.copy()
    out[100:760, 140:560] = img[100:760, 140:560]
    cv2.putText(out, "Tab Telmisartan 40", (160, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (70, 30, 20), 2, cv2.LINE_AA)
    return out


def test_stone_floor_regions_are_set_aside_and_writing_on_the_page_is_kept():
    img = _photo()
    regs = [Region(HANDWRITTEN, [150, 270, 450, 315], [], {}), Region(HANDWRITTEN, [10, 200, 110, 260], [], {}),
            Region(HANDWRITTEN, [570, 400, 598, 470], [], {})]
    nontext.classify_regions(regs, img)
    assert regs[0].kind == HANDWRITTEN and regs[0].features["paper_overlap"] > 0.9
    assert regs[1].kind == nontext.NON_TEXT and regs[1].features["paper_overlap"] < 0.3
    assert regs[2].kind == nontext.NON_TEXT


def test_a_page_that_fills_the_picture_excludes_nothing():
    img = np.full((400, 300, 3), (190, 150, 140), np.uint8)
    ev = nontext.PageEvidence(img)
    assert ev.mask.min() == 1


# ---- list numbers are not part of a test name
@pytest.mark.parametrize("raw,want", [("(1) CBC", ["CBC"]), ("2. LFT", ["LFT"]), ("3) TSH", ["TSH"]), ("[4] FBS", ["FBS"]),
                                      ("(1) CBC, (2) Urine R/E & C/S", ["CBC", "Urine R/E & C/S"]), ("HbA1c", ["HbA1c"]),
                                      ("Vitamin B12", ["Vitamin B12"]), ("25 OH Vitamin D", ["25 OH Vitamin D"])])
def test_list_numbers_are_stripped_from_test_names(raw, want):
    assert split_tests(raw) == want


# ---- advice that is really a medicine or a test
class _Ctx:
    def __init__(self): self.added = []; self.med_resolved = {}
    def add(self, **kw): self.added.append(kw); return "id"


def test_medicines_among_the_advice_are_dropped_and_tests_among_it_move_to_the_tests(monkeypatch):
    monkeypatch.setattr(X, "_misfiled_medicine", lambda t: t.lower().startswith(("t.", "inj", "c. ")))
    monkeypatch.setattr(X.lab_resolve, "resolve", lambda t: object() if "lipase" in (t or "").lower() else None)
    c = _Ctx()
    X._facts_prescription(c, {"advice": [{"text": "T. JARDANCE (25) OD"}, {"text": "INJ XULTOPHY 30U SC"},
                                         {"text": "S. Lipase-916"}, {"text": "Low salt diet"}], "investigations": []})
    kinds = [(a["fact_type"], a["local_text"]) for a in c.added]
    assert ("advice", "Low salt diet") in kinds and ("investigation_order", "S. Lipase-916") in kinds
    assert not any("JARDANCE" in t or "XULTOPHY" in t for _k, t in kinds)


# ---- tests written with the follow-up line
class _Client:
    def __init__(self, answer): self.answer, self.prompts = answer, []
    def vlm_json_ex(self, image, prompt, schema, **kw):
        self.prompts.append(prompt)
        return self.answer, "m"


def test_the_second_look_finds_the_tests_written_next_to_the_follow_up():
    cl = _Client({"tests": ["HbA1c", "FBS", "PPBS", "S. Lipase", "TSH", "LFT"]})
    got = R.followup_tests(cl, b"img", "To review after 2 wks", known=["TSH"])
    assert got == ["HbA1c", "FBS", "PPBS", "S. Lipase", "LFT"]                 # TSH was already listed
    assert "review after 2 wks" in cl.prompts[0] and "Do not add a test that is not written" in cl.prompts[0]


@pytest.mark.parametrize("answer", [{"tests": []}, {"tests": "HbA1c"}, {"tests": [None, 5, ""]}, {}, None])
def test_nothing_usable_in_the_answer_adds_nothing(answer):
    assert R.followup_tests(_Client(answer), b"img", "review after 2 wks", known=[]) == []


def test_no_follow_up_means_no_extra_call_and_a_failed_call_costs_nothing(monkeypatch):
    cl = _Client({"tests": ["HbA1c"]})
    assert R.followup_tests(cl, b"img", None, known=[]) == [] and R.followup_tests(cl, b"img", "  ", known=[]) == [] and cl.prompts == []
    class Boom:
        def vlm_json_ex(self, *a, **k): raise RuntimeError("down")
    assert R.followup_tests(Boom(), b"img", "review after 2 wks", known=[]) == []
    monkeypatch.setattr(settings, "followup_second_look", False)
    assert R.followup_tests(cl, b"img", "review after 2 wks", known=[]) == [] and cl.prompts == []


def test_medicines_and_advice_in_the_second_look_answer_are_not_taken_as_tests(monkeypatch):
    cl = _Client({"tests": ["HbA1c", "steam inhalation", "plenty of fluids"]})
    assert R.followup_tests(cl, b"img", "review after 2 wks", known=[]) == ["HbA1c"]


# ---- the screen shows only what is on the page
def test_the_tables_hide_values_that_are_not_on_the_page():
    assert 'if(none) return "";' in ADMIN_PAGE                                  # an absent value has no cell
    assert "const shown=rows.filter(r=>r[1]!==" in ADMIN_PAGE and "Nothing readable on the page." in ADMIN_PAGE


def test_the_follow_up_region_is_the_enlarged_lower_part_around_the_matching_line():
    import cv2
    import numpy as np

    img = np.full((1000, 700, 3), 255, np.uint8)
    cv2.putText(img, "To review after 2 wks", (60, 800), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    ok, png = cv2.imencode(".png", img)
    blocks = [{"text": "Dr Someone", "bbox": [50, 100, 300, 130]}, {"text": "To review after 2 wks", "bbox": [55, 780, 330, 810]}]
    out = cv2.imdecode(np.frombuffer(R.followup_region(png.tobytes(), blocks, "To review after 2 wks"), np.uint8), cv2.IMREAD_COLOR)
    assert out.shape[1] >= 1600                                                    # enlarged
    assert out.shape[0] < img.shape[0] * (out.shape[1] / img.shape[1]) * 0.5       # only the part around the line
    nomatch = cv2.imdecode(np.frombuffer(R.followup_region(png.tobytes(), [], "x review"), np.uint8), cv2.IMREAD_COLOR)
    assert nomatch.shape[1] >= 1600 and nomatch.shape[0] > 0                       # no match: the lower 40%
