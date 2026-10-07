"""This deployment does not use ABDM identification: the page is not asked for an ABHA id, nothing validates one
and nothing is matched on one. The switch is CDI_ABHA_ENABLED (like FHIR and the review screens)."""
from __future__ import annotations

from datetime import date

from cdi_adapter.config import settings
from cdi_adapter.extract import fields as F
from cdi_adapter.extract.prompt import build_extraction_prompt
import pytest


@pytest.fixture(autouse=True)
def _full_profile(monkeypatch):
    """These tests check the wording of the FULL extraction prompt; the slim profile has its own tests."""
    from cdi_adapter.config import settings as _s

    monkeypatch.setattr(_s, "extract_profile", "full")



PAGE = "Patient: Anil Mehra 54 y M ph 9830011234 ABHA 14-1111-2222-3333"
RAW = {"name": "Anil Mehra", "age_text": "54 y", "sex": "M", "phone": "9830011234", "abha_id": "14-1111-2222-3333"}


def test_with_abha_off_the_prompt_does_not_ask_for_it_and_says_to_leave_it_empty(monkeypatch):
    monkeypatch.setattr(settings, "abha_enabled", False)
    p = build_extraction_prompt("prescription", [])
    assert "`phone` and `address`" in p and "`address` and `abha_id`" not in p
    assert "leave `abha_id` null" in p


def test_with_abha_on_nothing_changes(monkeypatch):
    monkeypatch.setattr(settings, "abha_enabled", True)
    p = build_extraction_prompt("prescription", [])
    assert "`address` and `abha_id`" in p and "leave `abha_id` null" not in p


def test_with_abha_off_a_value_the_model_wrote_is_not_read_checked_or_flagged(monkeypatch):
    monkeypatch.setattr(settings, "abha_enabled", False)
    got = F.check_patient(RAW, PAGE, date(2026, 10, 7))["abha_id"]
    assert got == {"value": None, "status": F.ABSENT, "reason": None}


def test_with_abha_on_it_is_checked_against_the_page_as_before(monkeypatch):
    monkeypatch.setattr(settings, "abha_enabled", True)
    got = F.check_patient(RAW, PAGE, date(2026, 10, 7))["abha_id"]
    assert got["value"] == "14-1111-2222-3333" and got["status"] == F.CHECKED


def test_the_pod_settings_turn_it_off():
    from pathlib import Path
    env = (Path(__file__).resolve().parents[1] / ".env.runpod").read_text(encoding="utf-8")
    assert "CDI_ABHA_ENABLED=false" in env
