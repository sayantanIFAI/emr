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
    assert ("advice", "Low salt diet") in kinds and ("investigation_order", "S. Lipase") in kinds
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
    assert any("review after 2 wks" in p for p in cl.prompts) and all("Do not add a test that is not written" in p for p in cl.prompts)


@pytest.mark.parametrize("answer", [{"tests": []}, {"tests": "HbA1c"}, {"tests": [None, 5, ""]}, {}, None])
def test_nothing_usable_in_the_answer_adds_nothing(answer):
    assert R.followup_tests(_Client(answer), b"img", "review after 2 wks", known=[]) == []


def test_tests_are_looked_for_even_when_no_follow_up_was_found_and_a_failed_call_costs_nothing(monkeypatch):
    cl = _Client({"tests": ["HbA1c"]})
    assert R.followup_tests(cl, b"img", None, known=[]) == ["HbA1c"] and cl.prompts      # written anywhere, not only by the follow-up
    class Boom:
        def vlm_json_ex(self, *a, **k): raise RuntimeError("down")
    assert R.followup_tests(Boom(), b"img", "review after 2 wks", known=[]) == []
    monkeypatch.setattr(settings, "followup_second_look", False)
    cl2 = _Client({"tests": ["HbA1c"]})
    assert R.followup_tests(cl2, b"img", "review after 2 wks", known=[]) == [] and cl2.prompts == []


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


def test_the_page_is_looked_at_in_the_lower_left_and_right_views_too():
    import cv2
    import numpy as np

    img = np.full((1000, 700, 3), 255, np.uint8)
    ok, png = cv2.imencode(".png", img)
    views = R.page_views(png.tobytes())
    assert len(views) == 4                                                       # the page, the lower part, the left, the right
    sizes = [cv2.imdecode(np.frombuffer(v, np.uint8), cv2.IMREAD_COLOR).shape for v in views]
    assert sizes[0] == (1000, 700, 3) and all(s[1] >= 1400 for s in sizes[1:])  # the pieces are enlarged


def test_every_view_is_asked_and_the_answers_are_pooled():
    import cv2
    import numpy as np

    class Seq:
        def __init__(self): self.n = 0
        def vlm_json_ex(self, image, prompt, schema, **kw):
            self.n += 1
            return {"tests": [["HbA1c"], ["PPBS"], ["LFT", "HbA1c"], ["TSH"]][(self.n - 1) % 4]}, "m"
    ok, png = cv2.imencode(".png", np.full((800, 600, 3), 255, np.uint8))
    got = R.followup_tests(Seq(), png.tobytes(), None, known=[])
    assert sorted(got) == ["HbA1c", "LFT", "PPBS", "TSH"]


@pytest.mark.parametrize("text,expected", [
    ("{HbA1c", ["HbA1c"]), ("TSH}", ["TSH"]), ("[HbA1c / FBS / PPBS / TSH]", ["HbA1c", "FBS", "PPBS", "TSH"]),
    ("S Lipase-916", ["S Lipase"]), ("[Fructosamine L-216]", ["Fructosamine"]), ("HbA1c - 7.8", ["HbA1c"]),
    ("Vitamin B-12", ["Vitamin B-12"]), ("A/G ratio", ["A/G ratio"]), ("CBC/KFT/LFT", ["CBC", "KFT", "LFT"]),
    ("HbA1c, HbA1c", ["HbA1c"]),
])
def test_test_names_lose_braces_and_results_and_lists_are_split(text, expected):
    from cdi_adapter.extract.test_names import split_tests
    assert split_tests(text) == expected


def test_the_wider_look_keeps_only_names_the_lab_gate_places():
    cl = _Client({"tests": ["HbA1c", "I Revet su", "Sedox tin - 2.5w", "Reduced Hb", "NS - 30m", "Ok-pins", "FBS"]})
    got = R.followup_tests(cl, b"img", None, known=[])
    assert "HbA1c" in got and "FBS" in got
    assert not any(x in got for x in ("I Revet su", "Sedox tin - 2.5w", "NS - 30m", "Ok-pins"))


def test_a_misread_close_to_a_reference_test_stays_so_the_choice_step_can_pick_it(monkeypatch):
    monkeypatch.setattr(R.lab_resolve, "resolve", lambda t: None)
    monkeypatch.setattr(R.lab_resolve, "suggest", lambda t, k=5, floor=0.6: ["Free T4"] if "ft4" in (t or "").lower() else [])
    cl = _Client({"tests": ["Fl4 ft4", "Sedox tin"]})
    assert R.followup_tests(cl, b"img", None, known=[]) == ["Fl4 ft4"]


def test_a_test_found_twice_is_listed_once():
    c = _Ctx()
    X._facts_prescription(c, {"advice": [{"text": "S.Lipase"}], "investigations": [{"text": "S. Lipase"}, {"text": "HbA1c"},
                                                                                      {"text": "hba1c"}, {"text": "Lipase"}]})
    got = [a["local_text"] for a in c.added if a["fact_type"] == "investigation_order"]
    assert len(got) == 2 and "HbA1c" in got


def test_html_entities_the_model_wrote_are_turned_back_into_plain_text():
    got = X._unescape({"d": "Consultant Endocrinologist &amp; Diabetologist", "l": ["R&amp;D", "a < b", 5], "n": None, "x": {"y": "&quot;hi&quot;"}})
    assert got == {"d": "Consultant Endocrinologist & Diabetologist", "l": ["R&D", "a < b", 5], "n": None, "x": {"y": '"hi"'}}
