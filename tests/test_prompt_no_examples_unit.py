"""No real test or drug name may appear as an EXAMPLE in what the reader is shown.

A reader that cannot make out a handwritten line tends to answer with the example it was given, which would put a
test on a prescription that does not have it. Examples are described ("a line after 'Adv'"), never named.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract.prompt import build_extraction_prompt

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
NAMES = re.compile(r"(?<![A-Za-z0-9])(CBC|KFT|LFT|RFT|HbA1c|HBA1C|FBS|PPBS|TSH|ECG|EEG|Echo|CXR|X-ray|USG|MRI|Urea|Creatinine)"
                   r"(?![A-Za-z0-9])")


@pytest.mark.parametrize("doc_type", ["prescription", "opd_note", "referral"])
def test_the_extraction_prompt_names_no_test_as_an_example(doc_type, monkeypatch):
    for abha in (True, False):
        monkeypatch.setattr(settings, "abha_enabled", abha)
        found = NAMES.findall(build_extraction_prompt(doc_type, []))
        assert not found, f"{doc_type}: example test names in the prompt: {found}"


@pytest.mark.parametrize("schema", ["prescription.v3.json", "opd_note.v3.json"])
def test_the_investigations_field_description_names_no_test(schema):
    d = json.loads((SCHEMAS / schema).read_text(encoding="utf-8"))["properties"]["investigations"]["description"]
    assert not NAMES.search(d), d
    assert "not written on the page" in d                      # and it says what must not be done
