"""Medicines: advice is not a medicine, a misread brand is a CHOICE among reference names, nothing is free text."""
from __future__ import annotations

import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import medicine_resolve as MR
from cdi_adapter.extract import resolve_llm as R
from cdi_adapter.extract import service as X
from cdi_adapter.extract.medicine_lexicon import Lexicon


@pytest.fixture
def lexicon(monkeypatch):
    lex = Lexicon({"telmisartan", "cetirizine", "paracetamol", "pantoprazole", "montelukast", "zincovit", "sompraz", "oseltamivir",
                   "betadine", "amoxicillin", "cardiolock"})
    monkeypatch.setattr(MR, "load", lambda path=None: lex)
    monkeypatch.setattr("cdi_adapter.extract.medicine_lexicon.load", lambda path=None: lex)
    monkeypatch.setattr(MR, "medicine_match", lambda text, lx=None: (lex.match(MR.first_word(text)) if text else None))
    monkeypatch.setattr(MR.indian_codes, "drug_lookup", lambda t, path=None: None)
    monkeypatch.setattr(MR.indian_codes, "drugs", lambda path=None: None)
    monkeypatch.setattr(settings, "llm_resolve_medicines", True)     # off by default (lab tests are what matter)
    return lex


@pytest.mark.parametrize("text", ["steam inhalation", "goggle & warm water", "gargle with warm water and betadine", "enough fluids",
                                  "Rest for 3-5 days", "plenty of fluids ORS", "avoid sugary drinks"])
def test_advice_listed_among_the_medicines_is_recognised_as_advice(lexicon, text):
    assert MR.advice_like(text) is True


@pytest.mark.parametrize("text,has_dose", [("Tab. Paracetamol 650", False), ("Cap Sompraz D 1 cap BBF", False), ("steam tablet 500 mg", False),
                                           ("Zincovit", False), ("Rest tablet", True)])
def test_a_medicine_or_anything_with_a_dose_is_never_called_advice(lexicon, text, has_dose):
    assert MR.advice_like(text, has_dose=has_dose) is False


def test_the_advice_filter_uses_the_dose_fields_of_the_entry(lexicon):
    assert X._advice_listed_as_medicine({"drug_text": "steam inhalation"}) is True
    assert X._advice_listed_as_medicine({"drug_text": "steam inhalation", "strength": "5 ml"}) is False
    # the model puts a default frequency on everything it lists: that is not evidence of a medicine (measured on a real page)
    assert X._advice_listed_as_medicine({"drug_text": "goggle & warm water", "frequency_text": "qds", "form": "solution",
                                         "duration_days": None, "instructions": "k betadine"}) is True
    assert X._advice_listed_as_medicine({"drug_text": "Tab Montelukast", "strength": {"value": 10}}) is False
    assert X._advice_listed_as_medicine({"drug_text": "steam inhalation", "route": "oral", "form": "other"}) is True   # filler fields say nothing
    assert X._advice_listed_as_medicine("plenty of fluids") is True and X._advice_listed_as_medicine(None) is False


def test_a_misread_brand_gets_reference_candidates_best_first(lexicon):
    c = MR.suggest("Cap. Soupraz")
    assert c and c[0] == "Sompraz"
    assert "Telmisartan" in MR.suggest("Telmesarton 40") and MR.suggest("xyz") == [] and MR.suggest(None) == []
    assert MR.suggest("Cetirizine $40")[0] == "Cetirizine"


def test_only_names_the_lists_do_not_place_exactly_are_put_to_the_model(lexicon):
    items = MR.pending(["Tab. Paracetamol 650", "Cap. Soupraz", "steam inhalation", "Tab. Zincort", "qqqqqq"])
    names = [w for w, _c in items]
    assert "Cap. Soupraz" in names and "Tab. Zincort" in names
    assert "Tab. Paracetamol 650" not in names and "steam inhalation" not in names and "qqqqqq" not in names


class _Client:
    def __init__(self, answer): self.answer, self.calls = answer, []
    def vlm_json_ex(self, image, prompt, schema, **kw):
        self.calls.append(prompt)
        return self.answer, "m"


def test_the_model_only_chooses_an_offered_candidate_that_is_close_to_what_was_read(lexicon):
    cl = _Client({"choices": [1, 1]})
    out = R.resolve_medicines(cl, b"img", ["Cap. Soupraz", "Tab. Zincort"])
    assert out["Cap. Soupraz"] == "Sompraz" and out["Tab. Zincort"] == "Zincovit"
    assert "Sompraz" in cl.calls[0] and "Zincovit" in cl.calls[0] and "letters actually written" in cl.calls[0]


@pytest.mark.parametrize("answer", [{"choices": [None]}, {"choices": [9]}, {"choices": ["Doxycycline"]}, {"choices": [True]},
                                    {"choices": "x"}, {}, None])
def test_anything_but_a_valid_candidate_number_is_ignored(lexicon, answer):
    assert R.resolve_medicines(_Client(answer), b"img", ["Cap. Soupraz"]) == {}


def test_a_call_that_fails_costs_nothing(lexicon):
    class Boom:
        def vlm_json_ex(self, *a, **k): raise RuntimeError("gateway down")
    assert R.resolve_medicines(Boom(), b"img", ["Cap. Soupraz"]) == {}


def test_the_medicine_call_is_off_by_default_and_costs_nothing(lexicon, monkeypatch):
    monkeypatch.setattr(settings, "llm_resolve_medicines", False)
    cl = _Client({"choices": [1]})
    assert R.resolve_medicines(cl, b"img", ["Cap. Soupraz"]) == {} and cl.calls == []


def test_switched_off_means_no_call(lexicon, monkeypatch):
    monkeypatch.setattr(settings, "llm_resolve_enabled", False)
    cl = _Client({"choices": [1]})
    assert R.resolve_medicines(cl, b"img", ["Cap. Soupraz"]) == {} and cl.calls == []


def test_no_reference_lists_means_no_candidates_and_no_call(monkeypatch):
    monkeypatch.setattr(MR, "load", lambda path=None: None)
    monkeypatch.setattr(MR.indian_codes, "drugs", lambda path=None: None)
    cl = _Client({"choices": [1]})
    assert R.resolve_medicines(cl, b"img", ["Cap. Soupraz"]) == {} and cl.calls == []
