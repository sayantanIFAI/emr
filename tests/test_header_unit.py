"""The doctor's and the clinic's name from the printed header, when the model left them empty (extract/header.py)."""
from __future__ import annotations

import pytest

from cdi_adapter.extract import header as H


@pytest.mark.parametrize("line,expected", [
    ("Dr. Kumar Sourav", "Dr. Kumar Sourav"),                                               # MEASURED: real pages
    ("Dr.Kumar Sourav MBBSPATMDPATalMine ConsuPyin/Nerndogist KSHEALTHCARE /", "Dr. Kumar Sourav"),
    ("Dr.Debasis Giri", "Dr. Debasis Giri"),
    ("Prof.（Dr.)Aniruddha Majumder", "Prof. Dr. Aniruddha Majumder"),                   # a full-width bracket from the reader
    ("Dr. K. Sourav MD", "Dr. K. Sourav"), ("Dr. Giri Mob. 98302", "Dr. Giri"),
    ("Medicine OPD", None), ("Dr", None), ("Dr. MBBS MD", None), ("", None),
])
def test_the_doctors_name_is_the_name_words_after_dr_up_to_the_first_qualification(line, expected):
    assert H.doctor_name(line) == expected


@pytest.mark.parametrize("line,expected", [
    ("KSHEALTHCARE", "KS HEALTHCARE"), ("Core Clinic Mob.9830228483 ConsultantEndocrinolgist&Dlabetologist Reg.", "Core Clinic"),
    ("LIFE CENTRE POLYCLINIC", "LIFE CENTRE POLYCLINIC"),
    ("Sinus Vertigo Clinic|Ear Clinic|Allergy Clinic| Voice Clinic", None),                  # a list of clinics is not the organisation
    ("HORMONE SCIENCETO HEALTH", None), ("Contacts:+919836834614", None), ("Clinic", None),
])
def test_the_clinics_name_is_the_line_with_exactly_one_organisation_word(line, expected):
    assert H.clinic_name(line) == expected


def B(text, y0, y1):
    return {"text": text, "bbox": [4, y0, 700, y1]}


PAGE = [B("Dr. Kumar Sourav", 0, 60), B("KSHEALTHCARE", 70, 100), B("HORMONE SCIENCETO HEALTH", 110, 130), B("Tab. Glimepiride 10mg", 500, 560),
        B("Diabetic Clinic follow up here", 900, 960), B("x", 1000, 1100)]


def test_the_header_fills_the_doctor_and_the_clinic_when_the_model_left_them_empty():
    payload = {"patient": {"name": "x"}}
    assert H.fill(payload, PAGE) == ["doctor", "clinic"]
    assert payload["prescriber"]["name"] == "Dr. Kumar Sourav" and payload["prescriber"]["clinic"]["name"] == "KS HEALTHCARE"


def test_a_value_the_model_gave_is_never_replaced_and_only_the_top_of_the_page_is_read():
    payload = {"prescriber": {"name": "Dr. A Banerjee", "clinic": {"name": "City Hospital", "phone": "1"}}}
    assert H.fill(payload, PAGE) == [] and payload["prescriber"]["name"] == "Dr. A Banerjee"
    assert payload["prescriber"]["clinic"] == {"name": "City Hospital", "phone": "1"}
    low = {}
    assert H.fill(low, [B("Dr. Someone", 900, 960), B("pad", 0, 20), B("end", 1000, 1100)]) == []            # a "Dr" far down the page is not the letterhead
    assert H.fill({}, []) == [] and H.fill({"prescriber": "text"}, PAGE) == []


def test_only_the_empty_part_is_filled():
    payload = {"prescriber": {"name": "Dr. A Banerjee", "clinic": {"name": None, "address": "Patna"}}}
    assert H.fill(payload, PAGE) == ["clinic"]
    assert payload["prescriber"]["clinic"] == {"name": "KS HEALTHCARE", "address": "Patna"} and payload["prescriber"]["name"] == "Dr. A Banerjee"
