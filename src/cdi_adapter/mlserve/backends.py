from __future__ import annotations

import base64
import copy
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"

try:
    _COMMON_DEFS: dict[str, Any] = json.loads(
        (_SCHEMA_DIR / "common.defs.json").read_text(encoding="utf-8")
    ).get("$defs", {})
except Exception:  # noqa: BLE001
    _COMMON_DEFS = {}


def bundle_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a self-contained JSON Schema: every ``cdi:common.defs#/$defs/X`` $ref
    is rewritten to a local ``#/$defs/X`` and the referenced definitions (plus their
    transitive deps) are embedded on the root. XGrammar / vLLM guided decoding needs
    a single document with no external refs."""
    out = copy.deepcopy(schema)
    local_defs: dict[str, Any] = dict(out.get("$defs", {}))
    pending: list[str] = []

    def rewrite(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and "common.defs" in ref:
                name = ref.rsplit("/", 1)[-1]
                node["$ref"] = f"#/$defs/{name}"
                if name not in local_defs:
                    pending.append(name)
            for v in node.values():
                rewrite(v)
        elif isinstance(node, list):
            for v in node:
                rewrite(v)

    rewrite(out)
    while pending:
        name = pending.pop()
        if name in local_defs or name not in _COMMON_DEFS:
            continue
        d = copy.deepcopy(_COMMON_DEFS[name])
        rewrite(d)
        local_defs[name] = d
    if local_defs:
        out["$defs"] = local_defs
    return out


class Backend:
    name = "base"

    def info(self) -> dict[str, Any]:
        return {"backend": self.name}

    def generate(
        self, image_b64: str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class StubBackend(Backend):
    name = "stub"

    def generate(self, image_b64, prompt, *, max_tokens=512, json_schema=None):  # noqa: ANN001
        from ..ml.client import _stub_classify

        if json_schema and json_schema.get("$id") == "cdi:classification.v1":
            text = json.dumps(_stub_classify(prompt))
        else:
            text = "STUB TRANSCRIPTION\nline one\nline two"
        return {"text": text, "backend": self.name, "model": "stub", "usage": {}}


class HFQwenVLBackend(Backend):
    """transformers Qwen2.5-VL. Loads lazily on first request; falls back 7B -> 3B on OOM."""

    name = "hf"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._processor = None
        self._model_id: str | None = None
        self._device = "cuda"

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "model": self._model_id,
            "device": self._device,
            "loaded": self._model is not None,
        }

    def _load(self, model_id: str) -> None:
        import torch
        from transformers import AutoProcessor

        try:
            from transformers import AutoModelForImageTextToText as VLModel
        except ImportError:  # pragma: no cover - very old transformers
            from transformers import Qwen2_5_VLForConditionalGeneration as VLModel  # type: ignore

        dtype = getattr(torch, settings.vlm_dtype, torch.bfloat16)
        log.info("hf_load_start", model_id=model_id, dtype=str(dtype))
        t0 = time.time()
        self._model = VLModel.from_pretrained(
            model_id, torch_dtype=dtype, device_map=self._device,
            attn_implementation="sdpa", low_cpu_mem_usage=True,
        )
        self._model.eval()
        self._processor = AutoProcessor.from_pretrained(
            model_id, max_pixels=settings.vlm_max_pixels_ocr
        )
        self._model_id = model_id
        log.info("hf_load_done", model_id=model_id, seconds=round(time.time() - t0, 1))

    def _ensure(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                self._load(settings.vlm_model_id)
            except Exception as exc:  # noqa: BLE001  (OOM, download, arch)
                log.error("hf_primary_load_failed", error=str(exc)[:300])
                self._load(settings.vlm_fallback_model_id)

    def generate(self, image_b64, prompt, *, max_tokens=512, json_schema=None):  # noqa: ANN001
        import torch
        from PIL import Image
        from qwen_vl_utils import process_vision_info

        self._ensure()
        assert self._model is not None and self._processor is not None

        img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        sys_txt = (
            "You are a meticulous clinical document analyst. Transcribe and report "
            "only what is visibly present. Never invent values."
        )
        if json_schema is not None:
            prompt = (
                f"{prompt}\n\nReturn ONLY a single JSON object conforming to this JSON Schema:\n"
                f"{json.dumps(json_schema)}"
            )
        messages = [
            {"role": "system", "content": [{"type": "text", "text": sys_txt}]},
            {"role": "user", "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": prompt},
            ]},
        ]
        chat = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[chat], images=image_inputs, videos=video_inputs,
            padding=True, return_tensors="pt",
        ).to(self._device)

        with torch.inference_mode():
            out = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        gen = out[:, inputs["input_ids"].shape[1]:]
        text = self._processor.batch_decode(
            gen, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0].strip()
        return {
            "text": text,
            "backend": self.name,
            "model": self._model_id,
            "usage": {"prompt_tokens": int(inputs["input_ids"].shape[1]),
                      "completion_tokens": int(gen.shape[1])},
        }


class VLLMBackend(Backend):
    """Thin OpenAI-compatible client for a separate ``vllm serve`` process.

    vLLM holds the model with a paged KV cache and does continuous batching, so
    many ``/vlm/generate`` calls in flight share the GPU. ``json_schema`` is sent
    as ``guided_json`` (XGrammar) → the output is schema-valid by construction,
    which removes the repair / retry / ``_partial`` path on the client side.
    """

    name = "vllm"

    def __init__(self) -> None:
        import httpx

        self._base = settings.vllm_url.rstrip("/")
        self._model = settings.vllm_model or settings.vlm_model_id
        self._c = httpx.Client(timeout=settings.vllm_timeout_s)

    def info(self) -> dict[str, Any]:
        loaded = False
        served = None
        try:
            r = self._c.get(f"{self._base}/models", timeout=5.0)
            if r.status_code == 200:
                served = [m["id"] for m in r.json().get("data", [])]
                loaded = bool(served)
        except Exception as exc:  # noqa: BLE001
            log.warning("vllm_probe_failed", error=str(exc)[:200])
        return {"backend": self.name, "model": self._model, "device": "cuda",
                "loaded": loaded, "served": served, "guided_backend": settings.vllm_guided_backend}

    def generate(self, image_b64, prompt, *, max_tokens=512, json_schema=None):  # noqa: ANN001
        sys_txt = (
            "You are a meticulous clinical document analyst. Transcribe and report "
            "only what is visibly present. Never invent values."
        )
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": sys_txt},
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ]},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if json_schema is not None and settings.vllm_guided:
            # xgrammar enforces schema-VALIDITY at the token level. We deliberately
            # do NOT send a `strict` response_format: with our permissive schemas
            # (optional arrays, anyOf) strict mode makes the model terminate
            # list-heavy sections early. `guided_json` keeps output valid without
            # that pressure; the prompt still asks for completeness.
            body["guided_json"] = bundle_schema(json_schema)
            if settings.vllm_guided_backend:
                body["guided_decoding_backend"] = settings.vllm_guided_backend

        t0 = time.time()
        r = self._c.post(f"{self._base}/chat/completions", json=body)
        if r.status_code >= 400:
            log.error("vllm_http_error", status=r.status_code, body=r.text[:400])
        r.raise_for_status()
        data = r.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        usage = data.get("usage", {}) or {}
        return {
            "text": text,
            "backend": self.name,
            "model": data.get("model", self._model),
            "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens"),
                      "latency_s": round(time.time() - t0, 2)},
        }


def make_backend() -> Backend:
    b = settings.mlserve_backend
    if b == "stub":
        return StubBackend()
    if b == "vllm":
        return VLLMBackend()
    return HFQwenVLBackend()
