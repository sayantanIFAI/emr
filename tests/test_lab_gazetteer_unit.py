"""The lab-test gate: doctors' abbreviations -> the test and its LOINC code; anything else is not a lab test."""
from __future__ import annotations

import pytest

from cdi_adapter.extract import lab_gazetteer as G

TESTS = {
    "CBC": "58410-2", "KFT": "24362-4", "RFT": "24362-4", "LFT": "24325-1", "TSH": "11580-8", "FBS": "1558-6",
    "PPBS": "1521-4", "HbA1c": "4548-4", "Hb1AC": "4548-4", "ESR": "30341-2", "CRP": "1988-1", "Lipid profile": "24331-9",
    "Thyroid profile": "68997-6", "Urine R/E": "24357-4", "Stool RE": "10701-1", "Vit D3": "62292-8", "B12": "2132-9",
    "Dengue NS1": "57700-7", "Widal": "40673-6", "PT/INR": "5902-2", "HIV": "40438-4", "HBsAg": "5196-1", "APTT": "3173-2",
    "PSA": "2857-1", "Serum electrolytes": "24326-9", "Complete blood count": "58410-2", "Lipid profle": "24331-9",
}
NOT_TESTS = ["Candilock (lot)", "Cardiology", "Intravenous fluid therapy", "Staphylococcus", "Catheter", "Reaplerology",
             "IPOM. Ventral hernia", "Tab Paracetamol", "Zincovit", "Montana fx", "Inj DNS", "", None]


@pytest.mark.parametrize("name,loinc", TESTS.items())
def test_an_abbreviation_resolves_to_its_test_and_code(name, loinc):
    m = G.lookup(name)
    assert m is not None and m.kind == "test" and m.loinc == loinc, (name, m)


@pytest.mark.parametrize("name", ["Haemoglobin", "Reticulocyte count", "Platelet count", "Bleeding time", "CD4 test", "CA-125",
                                  "RBS", "Absolute eosinophil count (AEC)", "Creatinine", "Serum creatinine", "Uric acid", "Haemoglobn",
                                  "Platelet cuont"])
def test_a_test_from_the_catalogue_or_one_analyte_of_a_panel_is_recognised(name):
    assert G.lookup(name) is not None, name


@pytest.mark.parametrize("name", ["Creatinine", "Uric acid", "SGOT", "Sodium", "Haemoglobin"])
def test_a_single_analyte_never_gets_the_code_of_the_panel_it_belongs_to(name):
    m = G.lookup(name)
    assert m is not None and m.loinc is None, (name, m)


@pytest.mark.parametrize("name", NOT_TESTS)
def test_a_medicine_a_diagnosis_or_a_misreading_is_not_a_lab_test(name):
    assert G.lookup(name) is None, name


def test_a_short_abbreviation_is_never_matched_loosely():
    assert G.lookup("CBG") is None and G.lookup("LTF") is None and G.lookup("TSX") is None


def test_the_result_binds_the_code_of_a_recognised_abbreviation_and_sets_the_rest_aside():
    from cdi_adapter.extract.test_names import UNRECOGNISED
    from cdi_adapter.output import json_connector as jc

    facts = [{"fact_type": "investigation_order", "id": str(i), "local_text": t, "status": "needs_check", "code": None,
              "confidence_overall": 0.5} for i, t in enumerate(["KFT", "Reticulocyte count", "Candilock (lot)", "ECG"])]
    blocks = [{"id": "1", "text": "KFT  reticulocyte count  ECG  candilock"}]
    res = jc.build_result(jc.ResultInputs(document={"id": "d", "status": "validated", "original_filename": "x.jpg"},
                                          facts=facts, blocks=blocks, pages=[], payload={}))
    by = {t["as_written"]: t for t in res["lab_tests"]}
    assert by["KFT"]["code"] == "24362-4" and by["KFT"]["code_system"] == "http://loinc.org" and by["KFT"]["code_status"] == "bound"
    assert by["Reticulocyte count"]["code"] is None and not (by["Reticulocyte count"].get("reason") or "").startswith(UNRECOGNISED)
    assert by["ECG"]["status"] != "rejected" and not (by["ECG"].get("reason") or "").startswith(UNRECOGNISED)   # not in the tables, a known name
    assert by["Candilock (lot)"]["reason"].startswith(UNRECOGNISED)
