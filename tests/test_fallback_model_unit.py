"""The OOM fallback model (Qwen2-VL-7B) and what guards it. No GPU, no database.

Main OCR model = Qwen2.5-VL-7B-Instruct. The fallback loads only when the primary fails to load
(out of memory), in 8-bit, and anything it reads is held for review.
"""
from __future__ import annotations

import io
import json
import sys

import httpx
import jsonschema
import pytest

from cdi_adapter.config import Settings, settings
from cdi_adapter.ml import client as mc
from cdi_adapter.mlserve import backends as be
from cdi_adapter.recognition import engines as eng
from cdi_adapter.validate.service import fallback_findings

PRIMARY = "Qwen/Qwen2.5-VL-7B-Instruct"
FALLBACK = "Qwen/Qwen2-VL-7B-Instruct"


# ------------------------------------------------------------------ settings


def test_defaults_name_the_replacement_not_the_retired_model():
    s = Settings(_env_file=None)
    assert s.vlm_model_id == PRIMARY
    assert s.vlm_fallback_model_id == FALLBACK
    assert s.vlm_fallback_quantize == "8bit"
    assert s.gate_fallback_review is True
    assert "3B" not in s.vlm_model_id + s.vlm_fallback_model_id


def test_fallback_quantize_only_accepts_8bit_or_empty():
    with pytest.raises(ValueError):
        Settings(_env_file=None, vlm_fallback_quantize="4bit")


def test_quantize_for_follows_the_model_not_the_process():
    s = Settings(_env_file=None)
    assert be._quantize_for(PRIMARY, s) == ""            # the main model stays bf16
    assert be._quantize_for(FALLBACK, s) == "8bit"       # the fallback is as big: 8-bit
    assert be._quantize_for("other/model", s) == ""
    s2 = Settings(_env_file=None, vlm_fallback_quantize="")
    assert be._quantize_for(FALLBACK, s2) == ""
    # a fallback configured to the same id as the primary is just the primary
    s3 = Settings(_env_file=None, vlm_fallback_model_id=PRIMARY)
    assert be._quantize_for(PRIMARY, s3) == ""


# ------------------------------------------------------------------ gateway loading


def _backend(monkeypatch, fail_primary: bool):
    b = be.HFQwenVLBackend()
    log: list = []

    def fake_load(self, model_id):
        # the fallback must load AFTER the failed load's exception is gone, or its traceback
        # keeps the half-built primary's weights alive and the fallback runs out of memory too
        log.append(("load", model_id, sys.exc_info()[0] is None))
        if model_id == PRIMARY and fail_primary:
            raise RuntimeError("CUDA out of memory")
        self._model, self._processor, self._model_id = object(), object(), model_id

    monkeypatch.setattr(be.HFQwenVLBackend, "_load", fake_load)
    monkeypatch.setattr(be.HFQwenVLBackend, "_release", lambda self: log.append(("release",)))
    return b, log


def test_primary_loads_and_the_fallback_is_never_touched(monkeypatch):
    b, log = _backend(monkeypatch, fail_primary=False)
    b._ensure()
    assert log == [("load", PRIMARY, True)]
    assert b.info()["model"] == PRIMARY


def test_oom_loads_the_fallback_after_memory_is_released_and_outside_the_except(monkeypatch):
    b, log = _backend(monkeypatch, fail_primary=True)
    b._ensure()
    assert log == [("load", PRIMARY, True), ("release",), ("load", FALLBACK, True)]
    assert b.info()["model"] == FALLBACK       # /healthz and every response name the real model


def test_fallback_load_passes_8bit_config_and_primary_does_not(monkeypatch):
    transformers = pytest.importorskip("transformers")
    seen: dict[str, dict] = {}

    class FakeModel:
        def eval(self):
            return self

    def fake_from_pretrained(model_id, **kwargs):
        seen[model_id] = kwargs
        return FakeModel()

    monkeypatch.setattr(transformers.AutoModelForImageTextToText, "from_pretrained",
                        staticmethod(fake_from_pretrained))
    monkeypatch.setattr(transformers.AutoProcessor, "from_pretrained",
                        staticmethod(lambda *a, **k: object()))
    for model_id in (PRIMARY, FALLBACK):
        be.HFQwenVLBackend()._load(model_id)
    assert "quantization_config" not in seen[PRIMARY]
    assert seen[FALLBACK]["quantization_config"].load_in_8bit is True
    assert seen[FALLBACK]["attn_implementation"] == "sdpa"


# ------------------------------------------------------------------ security: untrusted-document notice


def test_system_notice_treats_the_document_as_data_never_instructions():
    n = be.SYSTEM_NOTICE.lower()
    assert "untrusted" in n and "never obey" in n and "ignore the previous instructions" in n
    assert "never invent" in n


def test_chat_messages_put_the_notice_first_in_each_shape():
    content = [{"type": "text", "text": "read this"}]
    plain = be._chat_messages(content)                       # OpenAI-compatible (vLLM)
    assert [m["role"] for m in plain] == ["system", "user"]
    assert plain[0]["content"] == be.SYSTEM_NOTICE and plain[1]["content"] == content
    parts = be._chat_messages(content, structured=True)      # transformers processor
    assert parts[0]["content"] == [{"type": "text", "text": be.SYSTEM_NOTICE}]


def test_vllm_requests_carry_the_notice(monkeypatch):
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"model": PRIMARY, "choices": [{"message": {"content": "x"}}]})

    b = be.VLLMBackend()
    b._c = httpx.Client(transport=httpx.MockTransport(handler))
    out = b.generate("aW1n", "transcribe")
    assert sent["messages"][0] == {"role": "system", "content": be.SYSTEM_NOTICE}
    assert sent["messages"][1]["role"] == "user"
    assert out["model"] == PRIMARY


# ------------------------------------------------------------------ the client reports who answered


def _http_client(model: str | None) -> mc.HttpMLClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"text": "Tab Metformin 500 mg", "backend": "hf"}
        if model is not None:
            body["model"] = model
        return httpx.Response(200, json=body)

    c = mc.HttpMLClient("http://gateway.test")
    c._c = httpx.Client(base_url="http://gateway.test", transport=httpx.MockTransport(handler))
    return c


def test_generate_ex_returns_the_model_the_gateway_reported():
    assert _http_client(FALLBACK).vlm_generate_ex(b"x", "p") == ("Tab Metformin 500 mg", FALLBACK)
    assert _http_client(None).vlm_generate_ex(b"x", "p")[1] is None
    assert _http_client(PRIMARY).vlm_generate(b"x", "p") == "Tab Metformin 500 mg"   # unchanged API


class _Scripted(mc._BaseClient):
    def __init__(self, replies, model=FALLBACK):
        self.replies, self.model = list(replies), model

    def vlm_generate(self, image, prompt, *, max_tokens=512, json_schema=None):
        return self.replies.pop(0)

    def vlm_generate_ex(self, image, prompt, *, max_tokens=512, json_schema=None):
        return self.vlm_generate(image, prompt), self.model


SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "confidence": {"type": "number"}},
          "required": ["name", "confidence"], "additionalProperties": False}


def test_vlm_json_ex_names_the_model_and_the_plain_call_still_returns_only_the_object():
    c = _Scripted(['{"name": "x", "confidence": 0.9}', '{"name": "x", "confidence": 0.9}'])
    obj, served = c.vlm_json_ex(None, "p", SCHEMA)
    assert obj == {"name": "x", "confidence": 0.9} and served == FALLBACK
    assert c.vlm_json(None, "p", SCHEMA) == obj          # no marker key leaks into the payload


def test_a_client_that_only_implements_generate_still_works():
    class Old(mc._BaseClient):
        def vlm_generate(self, image, prompt, *, max_tokens=512, json_schema=None):
            return '{"name": "x", "confidence": 1}'

    assert Old().vlm_json_ex(None, "p", SCHEMA) == ({"name": "x", "confidence": 1}, None)


# ------------------------------------------------------------------ provenance on every reading


class _LineClient:
    def __init__(self, model):
        self.model = model

    def vlm_generate_ex(self, image, prompt, *, max_tokens=512, json_schema=None):
        return "Tab Metformin 500 mg\nsecond line", self.model


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 12), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("served,expected", [(FALLBACK, FALLBACK), (PRIMARY, PRIMARY), (None, PRIMARY)])
def test_qwen_line_reading_carries_the_model_that_really_answered(monkeypatch, served, expected):
    monkeypatch.setattr("cdi_adapter.ml.client.get_client", lambda: _LineClient(served))
    (reading,) = eng.QwenLineEngine().recognize([_png()])
    assert reading.engine_version == expected and reading.text == "Tab Metformin 500 mg"


def test_is_fallback_model(monkeypatch):
    assert eng.is_fallback_model(FALLBACK) is True
    assert eng.is_fallback_model(PRIMARY) is False
    assert eng.is_fallback_model(None) is False and eng.is_fallback_model("stub") is False
    monkeypatch.setattr(settings, "vlm_fallback_model_id", PRIMARY)   # same id = not a fallback
    assert eng.is_fallback_model(PRIMARY) is False


# ------------------------------------------------------------------ S6: the fallback is always reviewed


def test_no_finding_for_the_primary_or_for_unrecorded_models():
    assert fallback_findings(PRIMARY, []) == []
    assert fallback_findings(None, [{"recognition": {"state": "agree"}}]) == []
    assert fallback_findings("stub", [{"recognition": None}, {}]) == []


def test_extraction_by_the_fallback_is_a_blocker():
    (sev, code, msg), = fallback_findings(FALLBACK, [])
    assert (sev, code) == ("blocker", "fallback-model") and FALLBACK in msg


def test_a_line_the_fallback_transcribed_is_a_blocker_even_when_extraction_used_the_primary():
    blocks = [{"recognition": {"state": "agree"}},
              {"recognition": {"state": "agree", "fallback_model": FALLBACK}}]
    assert [c for _s, c, _m in fallback_findings(PRIMARY, blocks)] == ["fallback-model"]


def test_the_hold_can_be_switched_off_only_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "gate_fallback_review", False)
    assert fallback_findings(FALLBACK, [{"recognition": {"fallback_model": FALLBACK}}]) == []


# ------------------------------------------------------------------ no patient value in a log or an error


PHI = "Anil Mehra 9876543210"


def test_schema_failure_logs_where_not_what(monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(mc.log, "warning", lambda event, **kw: seen.append((event, kw)))
    bad = json.dumps({"name": [PHI], "confidence": 0.9})        # a list where a string is wanted
    c = _Scripted([bad, bad])
    obj, _ = c.vlm_json_ex(None, "p", SCHEMA, retries=1)
    assert obj["_partial"] is True
    events = {e: kw for e, kw in seen}
    assert {"vlm_json_retry", "vlm_json_partial"} <= set(events)
    assert "Anil" not in repr(seen) and "9876543210" not in repr(seen)
    assert events["vlm_json_retry"]["error_at"] == "name" and events["vlm_json_partial"]["error_at"] == "name"


def test_errors_raised_to_callers_do_not_quote_the_page(monkeypatch):
    monkeypatch.setattr(mc.log, "warning", lambda *a, **k: None)
    with pytest.raises(mc.MLError) as e1:
        mc.extract_json(f"Patient {PHI} was seen today")
    assert "Anil" not in str(e1.value) and "chars" in str(e1.value)

    bad = json.dumps({"name": [PHI], "confidence": 0.9})
    with pytest.raises(mc.MLError) as e2:
        _Scripted([bad, bad]).vlm_json_ex(None, "p", SCHEMA, retries=1, lenient=False)
    assert "Anil" not in str(e2.value) and "9876543210" not in str(e2.value)
    assert "name" in str(e2.value)


def test_error_location():
    with pytest.raises(jsonschema.ValidationError) as e:
        jsonschema.validate({"tests": [{"name": [PHI]}]},
                            {"type": "object", "properties": {"tests": {"type": "array", "items": {
                                "type": "object", "properties": {"name": {"type": "string"}}}}}})
    assert mc.error_location(e.value) == "tests/0/name"
    with pytest.raises(jsonschema.ValidationError) as e:
        jsonschema.validate(PHI, {"type": "object"})
    assert mc.error_location(e.value) == "<root>"
