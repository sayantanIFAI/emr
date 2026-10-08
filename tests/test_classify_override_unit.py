"""A clinic's handwritten note the model called an operative note is a prescription when the page has no operative-note words (classify/service.py)."""
from __future__ import annotations

import pytest

from cdi_adapter.classify.service import override_misread_type

OP = {"doc_type": "operative_note", "confidence": 0.9, "is_handwritten": True, "rationale": "model"}
DENTAL = "Patient complains of pain\nRoot stump removal\n4 Difficulty in chewing Adv Digital OPG 2 FBS, BT CT\nFollow up after 1 week"


def test_an_operative_note_with_no_operative_words_and_prescription_marks_is_a_prescription():
    got = override_misread_type(OP, DENTAL)
    assert got["doc_type"] == "prescription" and got["confidence"] <= 0.8 and "overridden" in got["rationale"]
    assert OP["doc_type"] == "operative_note"                                        # the input is not changed


@pytest.mark.parametrize("text", [
    "Operative note\nProcedure performed: appendicectomy\nAdv: rest", "Surgeon: Dr X\nAnaesthesia: spinal\nAdv rest",
    "Estimated blood loss 50 ml\nTab paracetamol", "", None, "just some words with nothing else"])
def test_a_real_operative_note_or_a_page_with_no_prescription_marks_is_left_alone(text):
    assert override_misread_type(OP, text) == OP


@pytest.mark.parametrize("doc_type", ["prescription", "lab_report", "radiology_report", "discharge_summary"])
def test_only_operative_note_is_ever_overridden(doc_type):
    obj = dict(OP, doc_type=doc_type)
    assert override_misread_type(obj, DENTAL) == obj
