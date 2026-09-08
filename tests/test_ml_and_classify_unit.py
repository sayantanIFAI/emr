"""Unit tests for the model-gateway client and S2 prompt/parse. No infra, no GPU."""
from __future__ import annotations

import json

import jsonschema
import pytest

from cdi_adapter.classify.prompt import CLASSIFICATION_SCHEMA, build_classification_prompt
from cdi_adapter.ml.client import MLError, StubMLClient, extract_json


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_extract_json_embedded_prose():
    assert extract_json('Sure! Here it is:\n{"a": 3, "b": [1,2]}\nHope that helps') == {
        "a": 3,
        "b": [1, 2],
    }


def test_extract_json_missing_raises():
    with pytest.raises(MLError):
        extract_json("no json here at all")


def test_classification_schema_valid_metaschema():
    jsonschema.Draft202012Validator.check_schema(CLASSIFICATION_SCHEMA)


def test_prompt_lists_all_doc_types():
    p = build_classification_prompt(n_pages=2)
    for dt in ("prescription", "lab_report", "vitals_sheet", "discharge_summary", "other"):
        assert dt in p
    assert "2 page(s)" in p


def test_stub_client_returns_schema_valid_classification():
    c = StubMLClient()
    obj = c.vlm_json(
        b"fakeimg",
        build_classification_prompt(page_hint="HbA1c 7.8 %  Biochemistry  REF. RANGE"),
        CLASSIFICATION_SCHEMA,
    )
    jsonschema.validate(obj, CLASSIFICATION_SCHEMA)
    assert obj["doc_type"] == "lab_report"


def test_stub_client_prescription_and_handwritten_hint():
    c = StubMLClient()
    obj = c.vlm_json(
        b"x",
        build_classification_prompt(page_hint="Rx: Tab Metformin 500 mg (handwritten)"),
        CLASSIFICATION_SCHEMA,
    )
    assert obj["doc_type"] == "prescription"
    assert obj["is_handwritten"] is True
