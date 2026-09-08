from __future__ import annotations

import base64
import io
import json
import threading
import time
from typing import Any

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)


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


def make_backend() -> Backend:
    return StubBackend() if settings.mlserve_backend == "stub" else HFQwenVLBackend()
