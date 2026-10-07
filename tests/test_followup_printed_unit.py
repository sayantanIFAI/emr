"""Printed form text is not a doctor's instruction to come back; a written one still is."""
from __future__ import annotations

import pytest

from cdi_adapter.extract import fields as F

PAGE = "Please bring the prescription on the next visit  review after 2 weeks with reports  R/w c reports  F/U 2/3/24"


@pytest.mark.parametrize("text", ["Please bring the prescription on the next visit", "PLEASE BRING THE PRESCRIPTION ON THE NEXT VISIT",
                                  "Bring the prescription on the next visit", "For appointment call on 98300 12345"])
def test_printed_form_text_is_not_a_follow_up(text):
    got = F.parse_follow_up(text, PAGE.upper() + " FOR APPOINTMENT CALL ON 98300 12345")
    assert got["value"] is None and got["status"] == F.ABSENT and got["detail"]["text"] is None


@pytest.mark.parametrize("text,kind", [("review after 2 weeks with reports", "interval"), ("F/U 2/3/24", "date")])
def test_a_written_instruction_to_come_back_is_still_read(text, kind):
    got = F.parse_follow_up(text, PAGE)
    assert got["value"] == text and got["detail"]["kind"] == kind


def test_a_written_request_to_bring_reports_is_kept():
    assert F.parse_follow_up("R/w c reports", PAGE)["value"] == "R/w c reports"
