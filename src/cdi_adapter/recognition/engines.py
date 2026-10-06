"""Line recognizers. Each returns *transcription only* (text + confidence), never a code.

- ``TrOCREngine``   literal handwriting recognizer (transformers VisionEncoderDecoder, CPU).
- ``QwenLineEngine`` contextual reader: Qwen2.5-VL via the model gateway, one crop at a time,
  prompted WITHOUT any other engine's answer (no anchoring - ARCHITECTURE §15.3).
"""
from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass, field
from typing import Any

from .._cpu import THREADS_PER_TASK
from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)


@dataclass
class Reading:
    """One engine's reading of one crop. ``conf`` is None when the engine gives none -
    a VLM transcription has no calibrated confidence and we do not invent one."""

    engine: str
    engine_version: str
    text: str
    conf: float | None
    token_confidences: list[float] = field(default_factory=list)
    prompt_hash: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class EngineUnavailable(RuntimeError):
    pass


def is_fallback_model(model: str | None) -> bool:
    """True when ``model`` is the OOM fallback the gateway loaded instead of the primary
    (``vlm_fallback_model_id``). Anything it read is held for review (``gate_fallback_review``)."""
    return bool(model) and model != settings.vlm_model_id and model == settings.vlm_fallback_model_id


def _load_trocr_processor(model_id: str, processor_cls: Any) -> Any:
    """TrOCRProcessor, robust to transformers 5.x: its auto-loader only looks for a
    ``tokenizer.json``, which the Microsoft TrOCR repos do not ship (they ship the BPE
    ``vocab.json`` + ``merges.txt``). Fall back to building the RoBERTa tokenizer from those."""
    try:
        return processor_cls.from_pretrained(model_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("trocr_processor_fallback", model=model_id, error=str(exc)[:120])
    from huggingface_hub import hf_hub_download
    from transformers import AutoImageProcessor

    image_processor = AutoImageProcessor.from_pretrained(model_id)
    try:   # base/large checkpoints: RoBERTa byte-level BPE
        from transformers import RobertaTokenizer

        tok = RobertaTokenizer(vocab_file=hf_hub_download(model_id, "vocab.json"),
                               merges_file=hf_hub_download(model_id, "merges.txt"))
        return processor_cls(image_processor=image_processor, tokenizer=tok)
    except Exception:  # noqa: BLE001 - small checkpoints: raw sentencepiece
        return _SpmProcessor(image_processor, hf_hub_download(model_id, "sentencepiece.bpe.model"))


class _SpmProcessor:
    """TrOCR-small processor. transformers 5 cannot build its XLM-R tokenizer from the bare
    ``sentencepiece.bpe.model`` (it ends up with a 5-token vocab), so decode with
    sentencepiece directly. fairseq dictionary layout: 0 <s>, 1 <pad>, 2 </s>, 3 <unk>,
    then sentencepiece id + 1."""

    _OFFSET, _FIRST_REAL = 1, 4

    def __init__(self, image_processor: Any, spm_path: str) -> None:
        import sentencepiece as spm

        self.image_processor = image_processor
        self._sp = spm.SentencePieceProcessor(model_file=spm_path)
        self.tokenizer = type("_Tok", (), {"pad_token_id": 1})()

    def __call__(self, images: Any, return_tensors: str = "pt") -> Any:
        return self.image_processor(images=images, return_tensors=return_tensors)

    def batch_decode(self, sequences: Any, skip_special_tokens: bool = True) -> list[str]:
        out = []
        for seq in sequences.tolist() if hasattr(sequences, "tolist") else sequences:
            ids = [i - self._OFFSET for i in seq if i >= self._FIRST_REAL]
            out.append(self._sp.decode(ids))
        return out


class TrOCREngine:
    """microsoft/trocr-* on CPU. Stock checkpoints are English-only (IAM); a Bengali /
    mixed-script checkpoint is chosen by the E2-S10 benchmark and set via CDI_TROCR_MODEL_ID."""

    name = "trocr"

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.trocr_model_id
        self._model = None
        self._processor = None
        self._lock = threading.Lock()
        self._load_error: str | None = None
        self._device = "cpu"

    @property
    def version(self) -> str:
        return self.model_id

    def _load(self) -> None:
        if self._model is not None or self._load_error is not None:
            return
        with self._lock:
            if self._model is not None or self._load_error is not None:
                return
            try:
                import torch
                from transformers import TrOCRProcessor, VisionEncoderDecoderModel

                torch.set_num_threads(max(1, THREADS_PER_TASK))
                self._processor = _load_trocr_processor(self.model_id, TrOCRProcessor)
                model = VisionEncoderDecoderModel.from_pretrained(self.model_id)
                model.eval()
                self._device = self._pick_device(torch)
                model.to(self._device)
                self._model = model
                log.info("trocr_loaded", model=self.model_id, device=self._device,
                         threads=THREADS_PER_TASK)
            except Exception as exc:  # noqa: BLE001 - missing torch/weights must not crash the pipeline
                self._load_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.error("trocr_unavailable", model=self.model_id, error=self._load_error)

    @staticmethod
    def _pick_device(torch: Any) -> str:
        want = (settings.trocr_device or "cpu").strip().lower()
        if want in ("cuda", "auto"):
            if torch.cuda.is_available():
                return "cuda"
            if want == "cuda":
                log.warning("trocr_cuda_unavailable_using_cpu")
        return "cpu"

    def info(self) -> dict[str, Any]:
        return {"model": self.model_id, "loaded": self._model is not None,
                "device": getattr(self, "_device", None), "error": self._load_error}

    def recognize(self, crops_png: list[bytes]) -> list[Reading]:
        if not settings.trocr_enabled:
            return [Reading(self.name, self.version, "", None, error="trocr disabled")
                    for _ in crops_png]
        self._load()
        if self._model is None:
            return [Reading(self.name, self.version, "", None,
                            error=f"trocr unavailable: {self._load_error}") for _ in crops_png]
        import torch
        from PIL import Image

        out: list[Reading] = []
        bs = max(1, settings.trocr_batch_size)
        for i in range(0, len(crops_png), bs):
            batch = [Image.open(io.BytesIO(b)).convert("RGB") for b in crops_png[i:i + bs]]
            pix = self._processor(images=batch, return_tensors="pt").pixel_values.to(self._device)
            with torch.inference_mode():
                gen = self._model.generate(
                    pix, max_new_tokens=settings.trocr_max_new_tokens, num_beams=1,
                    do_sample=False, output_scores=True, return_dict_in_generate=True)
            texts = self._processor.batch_decode(gen.sequences, skip_special_tokens=True)
            trans = self._model.compute_transition_scores(
                gen.sequences, gen.scores, normalize_logits=True).float().cpu()
            pad_id = self._processor.tokenizer.pad_token_id
            for j, t in enumerate(texts):
                toks: list[float] = []
                # sequences[j][0] is the decoder start token; scores align with [1:]
                for k, tok in enumerate(gen.sequences[j][1:].tolist()):
                    if pad_id is not None and tok == pad_id:
                        break      # batch padding after this sequence's EOS
                    toks.append(math.exp(float(trans[j][k])))
                # geometric mean of token probabilities (incl. EOS) = sequence confidence
                conf = math.exp(sum(math.log(max(p, 1e-9)) for p in toks) / len(toks)) if toks else None
                out.append(Reading(self.name, self.version, t.strip(),
                                   round(conf, 4) if conf is not None else None,
                                   [round(p, 4) for p in toks]))
        return out


QWEN_LINE_PROMPT = (
    "Transcribe exactly the text written in this image crop. It is one line from a "
    "medical prescription and may be handwritten. Copy letters, numbers, units and "
    "dosing notation exactly as they appear. Do not correct spelling, expand "
    "abbreviations, or guess drug names. Write an unreadable character as ?. "
    "Output only the transcription on one line."
)


class QwenLineEngine:
    """Independent contextual reading of ONE crop through the model gateway.

    The prompt is a constant: it never carries another engine's candidate, so agreement
    with TrOCR is genuine independent evidence rather than anchoring."""

    name = "qwen2.5-vl"

    @property
    def version(self) -> str:
        """The configured model. A reading carries the model the gateway REPORTED for it: after
        an out-of-memory load that is the fallback, and recording the primary would be false."""
        return settings.vlm_model_id

    def recognize(self, crops_png: list[bytes]) -> list[Reading]:
        import hashlib

        from ..ml.client import get_client

        if settings.qwen_line_mode == "off":
            return [Reading(self.name, self.version, "", None, error="qwen line mode off")
                    for _ in crops_png]
        client = get_client()
        ph = hashlib.sha256(QWEN_LINE_PROMPT.encode()).hexdigest()[:16]
        out: list[Reading] = []
        for png in crops_png:
            try:
                txt, served = client.vlm_generate_ex(png, QWEN_LINE_PROMPT,
                                                     max_tokens=settings.qwen_line_max_tokens)
                line = next((s.strip() for s in (txt or "").splitlines() if s.strip()), "")
                out.append(Reading(self.name, served or self.version, line, None, prompt_hash=ph))
            except Exception as exc:  # noqa: BLE001
                out.append(Reading(self.name, self.version, "", None, prompt_hash=ph,
                                   error=str(exc)[:200]))
        return out
