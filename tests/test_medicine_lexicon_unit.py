"""The medicine-name list keeps a medicine out of the lab tests, with a small stand-in list (the real one is not in git)."""
from __future__ import annotations

import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import medicine_lexicon as ML

WORDS = ["augmentin", "pantoprazole", "metformin", "paracetamol", "cetirizine", "montana", "azithromycin", "dolo"]


@pytest.fixture()
def lex(tmp_path, monkeypatch):
    f = tmp_path / "names.txt"
    f.write_text("\n".join(WORDS) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "medicine_lexicon_path", str(f))
    ML._cache.clear()
    yield ML.load()
    ML._cache.clear()


@pytest.mark.parametrize("text,hit", [("Augmentin 625", "augmentin"), ("Tab. Metformin 500", "metformin"), ("Inj Pantoprazole 40", "pantoprazole"),
                                      ("In Dolo 650", "dolo"), ("Pantaprazole", "pantoprazole"), ("Azithromicin 500", "azithromycin"),
                                      ("Cap Cetrizine", "cetirizine")])
def test_a_medicine_name_is_found_even_misread_and_behind_a_form_prefix(lex, text, hit):
    assert ML.medicine_match(text, lex) == hit


@pytest.mark.parametrize("text", ["CBC", "KFT", "Blood for fever profile", "Serum creatinine", "Candilock (lot)", "ECG", "", None, "Tab"])
def test_a_test_or_an_unknown_name_is_not_found(lex, text):
    assert ML.medicine_match(text, lex) is None


def test_only_the_first_word_after_the_prefix_counts(lex):
    assert ML.medicine_match("Fever profile dolo", lex) is None          # a drug word later in a test name proves nothing


def test_without_a_list_nothing_is_ever_flagged(monkeypatch):
    monkeypatch.setattr(settings, "medicine_lexicon_path", "")
    ML._cache.clear()
    assert ML.load() is None and ML.medicine_match("Augmentin 625") is None
    monkeypatch.setattr(settings, "medicine_lexicon_path", "/no/such/file.txt")
    assert ML.load() is None


def test_the_result_rejects_a_medicine_from_the_list_and_names_it(lex):
    from cdi_adapter.output import json_connector as jc

    facts = [{"fact_type": "investigation_order", "id": str(i), "local_text": t, "status": "needs_check", "code": None,
              "confidence_overall": 0.5} for i, t in enumerate(["CBC", "Pantaprazole", "Dolo 650"])]
    res = jc.build_result(jc.ResultInputs(document={"id": "d", "status": "validated", "original_filename": "x.jpg"},
                                          facts=facts, blocks=[{"id": "1", "text": "CBC pantaprazole dolo 650"}], pages=[], payload={}))
    by = {t["as_written"]: t for t in res["lab_tests"]}
    assert by["CBC"]["status"] != "rejected"
    assert by["Pantaprazole"]["status"] == "rejected" and "pantoprazole" in by["Pantaprazole"]["reason"]
    assert by["Dolo 650"]["status"] == "rejected"
