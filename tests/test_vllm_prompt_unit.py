"""The model must SEE the shape it is asked for, on every backend; and an OPD note can carry its tests."""
from __future__ import annotations

import json
from pathlib import Path

from cdi_adapter.config import settings
from cdi_adapter.extract import prompt as P
from cdi_adapter.mlserve import backends as B

SCHEMA = {"type": "object", "properties": {"patient": {"type": "object"}}}


def test_the_schema_is_part_of_the_prompt_when_one_is_given():
    out = B.with_schema_instruction("Extract.", SCHEMA)
    assert out.startswith("Extract.") and "conforming to this JSON Schema" in out and json.dumps(SCHEMA) in out
    assert B.with_schema_instruction("Just look.", None) == "Just look."


def test_the_vllm_request_carries_the_schema_in_the_prompt_and_uses_the_current_api(monkeypatch):
    sent = {}

    class _R:
        status_code = 200

        def raise_for_status(self): ...

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}], "model": "m", "usage": {}}

    class _C:
        def post(self, url, json=None, **k):  # noqa: A002
            sent["body"] = json
            return _R()

    monkeypatch.setattr(settings, "vllm_guided", True)
    monkeypatch.setattr(settings, "vllm_guided_api", "structured_outputs")
    be = B.VLLMBackend()
    be._c = _C()
    be.generate("aGk=", "Extract.", json_schema=SCHEMA)
    body = sent["body"]
    assert "conforming to this JSON Schema" in json.dumps(body["messages"])           # the model sees the shape
    assert "structured_outputs" in body and "guided_json" not in body       # current vLLM ignores guided_json

    monkeypatch.setattr(settings, "vllm_guided_api", "guided_json")           # old vLLM only
    be.generate("aGk=", "Extract.", json_schema=SCHEMA)
    assert "guided_json" in sent["body"] and "structured_outputs" not in sent["body"]

    monkeypatch.setattr(settings, "vllm_guided", False)                        # the pod's setting
    be.generate("aGk=", "Extract.", json_schema=SCHEMA)
    assert not {"guided_json", "structured_outputs"} & set(sent["body"])


def test_an_opd_note_can_carry_its_ordered_tests_and_the_prompt_says_where_they_go():
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "opd_note.v3.json").read_text(encoding="utf-8"))
    assert {"investigations", "investigation_preparation"} <= set(schema["properties"])
    assert "`investigations`" in P._EXTRA["opd_note"] and "NOT `advice`" in P._EXTRA["opd_note"]


def _fake_server(monkeypatch, answers):
    """A vLLM that answers the posts in turn: [(text, finish_reason), ...]; returns the bodies it was sent."""
    seen: list[dict] = []

    class _R:
        status_code = 200

        def __init__(self, text, why):
            self._d = {"choices": [{"message": {"content": text}, "finish_reason": why}], "model": "m", "usage": {}}

        def raise_for_status(self): ...

        def json(self):
            return self._d

    class _C:
        def post(self, url, json=None, **k):  # noqa: A002
            seen.append(json)
            return _R(*answers[min(len(seen) - 1, len(answers) - 1)])

    monkeypatch.setattr(settings, "vllm_retry_on_length", True)
    be = B.VLLMBackend()
    be._c = _C()
    return be, seen


def test_an_answer_that_hits_the_token_limit_is_retried_once_and_the_finished_one_is_used(monkeypatch):
    be, seen = _fake_server(monkeypatch, [("looping looping", "length"), ('{"ok": 1}', "stop")])
    out = be.generate("aGk=", "Extract.", json_schema=SCHEMA)
    assert out["text"] == '{"ok": 1}' and len(seen) == 2
    assert seen[1]["repetition_penalty"] == settings.vllm_retry_repetition_penalty and "repetition_penalty" not in seen[0]


def test_when_the_retry_also_runs_out_the_first_answer_is_kept_and_nothing_more_is_sent(monkeypatch):
    be, seen = _fake_server(monkeypatch, [("first", "length"), ("second", "length")])
    assert be.generate("aGk=", "Extract.", json_schema=SCHEMA)["text"] == "first" and len(seen) == 2


def test_a_normal_answer_costs_one_request_and_free_text_is_never_retried(monkeypatch):
    be, seen = _fake_server(monkeypatch, [('{"ok": 1}', "stop")])
    be.generate("aGk=", "Extract.", json_schema=SCHEMA)
    assert len(seen) == 1
    be, seen = _fake_server(monkeypatch, [("a long line", "length")])
    be.generate("aGk=", "Read this line.")                       # no schema: a line read is not retried
    assert len(seen) == 1
