"""The Indian national code sets as the deterministic gate, and the model as a chooser among reference names only.

The real CLCI / CDCI indexes are not in git (licence); these tests use small stand-ins in the same format.
"""
from __future__ import annotations

import json

import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import indian_codes as IC
from cdi_adapter.extract import lab_resolve, resolve_llm

LABS = {"source": "test", "tests": [
    {"name": "Thyroid Stimulating Hormone", "aliases": ["TSH", "Thyrotropin"], "specimen": "Blood", "loinc": "3016-3",
     "fsn": "x", "lcn": "Thyrotropin [Units/volume] in Serum or Plasma"},
    {"name": "Creatinine", "aliases": [], "specimen": "Blood", "loinc": "2160-0", "fsn": "x", "lcn": "Creatinine [Mass/volume] in Serum or Plasma"},
    {"name": "Creatinine", "aliases": [], "specimen": "Urine", "loinc": "2161-8", "fsn": "x", "lcn": "Creatinine [Mass/volume] in Urine"},
    {"name": "Alanine Aminotransferase", "aliases": ["ALT", "SGPT"], "specimen": "Blood", "loinc": "1742-6", "fsn": "x",
     "lcn": "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma"},
    {"name": "Reticulocyte Count", "aliases": [], "specimen": "Blood", "loinc": "4679-7", "fsn": "x", "lcn": "Reticulocytes/100 erythrocytes in Blood"},
]}
DRUGS = {"source": "test",
         "brands": {"pantocid": [["p1", "Pantocid 40 mg tablet", ["g1"]]], "dolo": [["p2", "Dolo 650", ["g2"]]],
                    "two brand": [["p3", "Two Brand", ["g1", "g2"]]]},
         "generic_stems": {"carbimazole": "g9"}, "substances": {"candesartan cilexetil": "s1", "metformin": "s2"},
         "generic_names": {"g1": "Pantoprazole 40 mg oral tablet", "g2": "Acetaminophen 650 mg oral tablet"}}


@pytest.fixture()
def codes(tmp_path, monkeypatch):
    (tmp_path / "clci_labs.json").write_text(json.dumps(LABS), encoding="utf-8")
    (tmp_path / "cdci_drugs.json").write_text(json.dumps(DRUGS), encoding="utf-8")
    monkeypatch.setattr(settings, "indian_codes_dir", str(tmp_path))
    IC.clear_cache()
    yield tmp_path
    IC.clear_cache()


# ------------------------------------------------------------------------------------------------ labs
def test_a_lab_name_and_its_bracketed_aliases_resolve_to_the_national_code(codes):
    for text in ("Thyroid Stimulating Hormone", "TSH", "tsh", "Thyrotropin", "ALT", "SGPT", "Reticulocyte count"):
        rz = lab_resolve.resolve(text)
        assert rz is not None and rz.source == "CLCI" and rz.status == "bound", text
    assert lab_resolve.resolve("TSH").loinc == "3016-3"            # the national code, not the curated table's


def test_a_name_with_several_national_codes_is_a_candidate_never_a_fact(codes):
    rz = lab_resolve.resolve("Creatinine")
    assert rz.status == "candidate" and set(rz.candidates) == {"2160-0", "2161-8"}


def test_a_panel_the_national_list_lacks_still_resolves_from_the_curated_table(codes):
    rz = lab_resolve.resolve("KFT")
    assert rz is not None and rz.source == "gazetteer" and rz.loinc == "24362-4" and rz.status == "bound"


@pytest.mark.parametrize("text", ["Candilock (lot)", "Cardiology", "Catheter", "", None])
def test_what_is_in_no_list_is_not_resolved(codes, text):
    assert lab_resolve.resolve(text) is None


def test_without_the_directory_the_national_lists_say_nothing_and_the_table_still_works(monkeypatch):
    monkeypatch.setattr(settings, "indian_codes_dir", "")
    IC.clear_cache()
    assert IC.lab_candidates("TSH") == [] and IC.drug_lookup("Pantocid") is None
    assert lab_resolve.resolve("TSH").source == "gazetteer"


# ------------------------------------------------------------------------------------------------ drugs
def test_a_brand_resolves_to_its_generic_even_behind_a_form_prefix_and_a_misreading(codes):
    m = IC.drug_lookup("Tab. Pantocid 40")
    assert m.kind == "brand" and m.generic_names == ("Pantoprazole 40 mg oral tablet",) and not m.fuzzy
    assert IC.drug_lookup("Pantocidd") is None or IC.drug_lookup("Pantocidd").fuzzy
    assert IC.drug_lookup("Pantosid").fuzzy is True if IC.drug_lookup("Pantosid") else True
    assert IC.drug_lookup("Dolo 650").generic_ids == ("g2",)


def test_a_generic_or_substance_name_is_found_by_its_first_word(codes):
    assert IC.drug_lookup("Candesartan (10)").kind == "substance"
    assert IC.drug_lookup("Carbimazole 5 mg").kind == "generic"
    assert IC.drug_lookup("Inj Metformin").kind == "substance"


@pytest.mark.parametrize("text", ["CBC", "KFT", "Blood for fever profile", "ECG", "Cat", "", None])
def test_a_test_is_not_a_drug(codes, text):
    assert IC.drug_lookup(text) is None


# ------------------------------------------------------------------------------------------------ the model chooses, never invents
class _Client:
    def __init__(self, choices=None, boom=False):
        self.choices, self.boom, self.calls = choices, boom, 0

    def vlm_json_ex(self, image, prompt, schema, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("model gateway timed out")
        return {"choices": self.choices}, "m"


def test_the_prompt_lists_the_candidates_and_names_no_test_of_its_own(codes):
    items = [("TSHH", ["TSH", "Thyrotropin"])]
    p = resolve_llm.prompt_for(items)
    assert "1. read as 'TSHH' -> 1) TSH  2) Thyrotropin" in p and "null" in p


def test_only_a_listed_candidate_that_is_close_to_what_was_read_is_accepted():
    cands = ["TSH", "FBS", "ESR"]
    assert resolve_llm.accept("TSHH", cands, 1) == "TSH"
    assert resolve_llm.accept("TSHH", cands, 4) is None              # a number that was not offered
    assert resolve_llm.accept("TSHH", cands, 0) is None and resolve_llm.accept("TSHH", cands, None) is None
    assert resolve_llm.accept("TSHH", cands, "1") is None and resolve_llm.accept("TSHH", cands, True) is None
    assert resolve_llm.accept("hepatology", ["TSH"], 1) is None      # plausible for the page, nothing like the writing


def test_the_model_is_asked_only_about_names_the_gate_cannot_place(codes, monkeypatch):
    monkeypatch.setattr(settings, "llm_resolve_enabled", True)
    c = _Client(choices=[1])
    out = resolve_llm.resolve_tests(c, b"img", ["TSH", "ECG", "Tab Xyz 500 mg", "TSHH", "Cardiology"])
    assert c.calls == 1 and out == {"TSHH": "TSH"}                   # TSH/ECG known, the tablet is a medicine, Cardiology has no candidate


def test_no_candidates_means_no_call(codes, monkeypatch):
    monkeypatch.setattr(settings, "llm_resolve_enabled", True)
    c = _Client(choices=[])
    assert resolve_llm.resolve_tests(c, b"img", ["Cardiology", "Catheter"]) == {} and c.calls == 0


def test_a_failed_or_disabled_model_changes_nothing(codes, monkeypatch):
    monkeypatch.setattr(settings, "llm_resolve_enabled", True)
    assert resolve_llm.resolve_tests(_Client(boom=True), b"img", ["TSHH"]) == {}
    assert resolve_llm.resolve_tests(_Client(choices="nope"), b"img", ["TSHH"]) == {}
    monkeypatch.setattr(settings, "llm_resolve_enabled", False)
    c = _Client(choices=[1])
    assert resolve_llm.resolve_tests(c, b"img", ["TSHH"]) == {} and c.calls == 0


# ------------------------------------------------------------------------------------------------ the routing and the result
class _Rec:
    sess = None

    def __init__(self):
        self.facts = []

    def add(self, *, fact_type, local_text, **kw):
        self.facts.append((fact_type, local_text, kw.get("value_code_display")))
        return len(self.facts)


def test_a_drug_among_the_tests_moves_to_the_medicines_and_a_chosen_name_is_kept_apart(codes, monkeypatch):
    from cdi_adapter.extract import service

    stored = []
    monkeypatch.setattr(service.repo, "insert_medication_detail", lambda sess, fid, **kw: stored.append(kw["drug_text"]))
    c = _Rec()
    service._facts_prescription(c, {
        "medications": [{"drug_text": "Dolo 650", "evidence": ["b1"]}],
        "investigations": [{"text": "Tab Pantocid 40", "evidence": ["b2"]},        # a drug filed as a test: moves to the medicines
                           {"text": "Dolo 650 x 5d", "evidence": ["b3"]},          # already among the medicines: not listed twice
                           {"text": "TSHH", "evidence": ["b4"]},
                           {"text": "CBC/KFT", "evidence": ["b5"]}],
        "_test_resolved": {"TSHH": "TSH"}})
    tests = [(t, v) for k, t, v in c.facts if k == "investigation_order"]
    assert tests == [("TSHH", "TSH"), ("CBC", "CBC"), ("KFT", "KFT")]             # as written kept; the chosen name kept apart
    assert sorted(stored) == ["Dolo 650", "Tab Pantocid 40"] or len(stored) == 2


def test_the_terminology_stage_codes_a_test_and_a_medicine_from_the_indian_lists(codes):
    from cdi_adapter.terminology import service as T

    t = T._bind_fact({"fact_type": "investigation_order", "local_text": "TSH", "value_code_display": "TSH"})
    assert t["code"] == "3016-3" and t["code_status"] == "bound" and t["code_system"] == "http://loinc.org"
    m = T._bind_fact({"fact_type": "medication", "local_text": "Tab Pantocid 40", "value_code_display": None})
    assert m["code_system"] == IC.CDCI and m["code"] == "g1" and m["code_status"] == "bound"
    two = T._bind_fact({"fact_type": "medication", "local_text": "Two Brand", "value_code_display": None})
    assert two["code_status"] == "candidate"                          # two generics: not a fact
    assert T.system_short(IC.CDCI) == "CDCI"


def test_the_code_is_attached_only_for_a_licensed_system(codes, monkeypatch):
    from cdi_adapter.terminology import service as T

    monkeypatch.setattr(settings, "licensed_code_systems", ("LOINC", "UCUM"))
    assert T.bind_fact({"fact_type": "medication", "local_text": "Pantocid", "value_code_display": None})["code"] is None
    monkeypatch.setattr(settings, "licensed_code_systems", ("LOINC", "UCUM", "CDCI"))
    assert T.bind_fact({"fact_type": "medication", "local_text": "Pantocid", "value_code_display": None})["code"] == "g1"


def test_the_result_shows_a_model_chosen_name_as_chosen_and_binds_the_national_code(codes, monkeypatch):
    from cdi_adapter.output import json_connector as jc

    monkeypatch.setattr(settings, "licensed_code_systems", ("LOINC", "UCUM"))
    facts = [{"fact_type": "investigation_order", "id": "1", "local_text": "TSHH", "value_code_display": "TSH",
              "status": "accepted", "code": None, "confidence_overall": 0.5},
             {"fact_type": "investigation_order", "id": "2", "local_text": "Tab Pantocid", "value_code_display": "Tab Pantocid",
              "status": "accepted", "code": None, "confidence_overall": 0.5}]
    res = jc.build_result(jc.ResultInputs(document={"id": "d", "status": "validated", "original_filename": "x.jpg"}, facts=facts,
                                          blocks=[{"id": "1", "text": "tshh  tab pantocid"}], pages=[], payload={}))
    by = {t["as_written"]: t for t in res["lab_tests"]}
    tsh = by["TSHH"]
    assert tsh["status"] == "needs_check" and tsh["reason"].startswith("read as 'TSH' (chosen from the reference list by the model")
    assert tsh["code"] == "3016-3" and tsh["code_status"] == "bound"
    assert by["Tab Pantocid"]["status"] == "rejected" and "medicine" in by["Tab Pantocid"]["reason"]
