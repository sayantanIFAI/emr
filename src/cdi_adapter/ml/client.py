from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx
import jsonschema
from pathlib import Path

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"


def _build_registry():
    """Registry that resolves the shared 'cdi:common.defs' $ref used by extraction schemas."""
    try:
        from referencing import Registry, Resource

        reg = Registry()
        for f in _SCHEMA_DIR.glob("*.json"):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if "$id" in doc:
                reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
        return reg
    except Exception as exc:  # noqa: BLE001 - very old jsonschema
        log.warning("schema_registry_unavailable", error=str(exc)[:120])
        return None


_REGISTRY = _build_registry()


def validate_schema(obj: Any, schema: dict) -> None:
    if _REGISTRY is not None:
        jsonschema.Draft202012Validator(schema, registry=_REGISTRY).validate(obj)
    else:  # best effort without $ref resolution
        jsonschema.validate(obj, schema)


class MLError(RuntimeError):
    pass


def _b64(image: bytes | str) -> str:
    if isinstance(image, str):
        return image
    return base64.b64encode(image).decode("ascii")


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort: parse the first JSON object in a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_RE.search(text)
    if not m:
        raise MLError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def _stub_classify(prompt: str) -> dict[str, Any]:
    """Deterministic classification for tests: keyword-match ONLY the OCR sample block."""
    lo = prompt.lower()
    sample = lo
    if "<<<ocr_sample>>>" in lo and "<<<end_ocr_sample>>>" in lo:
        sample = lo.split("<<<ocr_sample>>>", 1)[1].split("<<<end_ocr_sample>>>", 1)[0]
    if any(k in sample for k in ("biochemistry", "hba1c", "ref. range", "haematology", "lab")):
        dt = "lab_report"
    elif any(k in sample for k in ("rx:", "rx ", "tab ", "tab.", "prescription", "1-0-1")):
        dt = "prescription"
    elif any(k in sample for k in ("vitals", "spo2", "pulse", "intake sheet")):
        dt = "vitals_sheet"
    elif "discharge" in sample:
        dt = "discharge_summary"
    else:
        dt = "other"
    garble = sample.count("[??]")
    handwritten = garble >= 3 or (dt == "prescription" and "1-0-1" not in sample and len(sample) < 80)
    return {
        "doc_type": dt,
        "specialty": None,
        "is_handwritten": handwritten,
        "languages": ["en"],
        "page_spans": [],
        "confidence": 0.66,
        "rationale": "stub keyword match on OCR sample",
    }


try:
    _COMMON_DEFS = json.loads((_SCHEMA_DIR / "common.defs.json").read_text(encoding="utf-8"))["$defs"]
except Exception:  # noqa: BLE001
    _COMMON_DEFS = {}


def _resolve(schema: Any) -> Any:
    """Inline a local/common.defs $ref so repair_payload can see its properties."""
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 5:
        ref = schema["$ref"]
        name = ref.rsplit("/", 1)[-1]
        nxt = _COMMON_DEFS.get(name)
        if nxt is None:
            return schema
        schema = nxt
        seen += 1
    return schema


def repair_payload(obj: Any, schema: dict | None = None) -> Any:
    """Best-effort coercion of common VLM JSON slips before validation."""
    schema = _resolve(schema)
    if isinstance(obj, dict):
        props = (schema or {}).get("properties") if isinstance(schema, dict) else None
        addl_false = isinstance(schema, dict) and schema.get("additionalProperties") is False
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if addl_false and props is not None and k not in props and not k.startswith("_"):
                # value put under a bogus key -> try to rehome into 'text' if that's expected/missing
                if props and "text" in props and "text" not in obj and isinstance(v, str):
                    out["text"] = v
                continue
            sub = props.get(k) if props else None
            if isinstance(sub, dict) and "items" in sub:
                out[k] = [repair_payload(x, sub["items"]) for x in (v or [])] if isinstance(v, list) else v
            else:
                out[k] = repair_payload(v, sub if isinstance(sub, dict) else None)
        # a "coded"/entry object with evidence but no text -> borrow from code/display/name,
        # but only when this object's schema actually declares a "text" property
        _wants_text = (not isinstance(schema, dict)) or ("text" in (schema.get("properties") or {}))
        if _wants_text and "evidence" in out and "text" not in out:
            for cand in ("code", "display", "concept", "name"):
                if isinstance(out.get(cand), str) and out[cand].strip():
                    out["text"] = out[cand]
                    if isinstance(schema, dict) and (schema.get("properties") or {}).get(cand) is None:
                        out.pop(cand, None)
                    break
        # required top-level confidence fields
        if isinstance(schema, dict):
            req = schema.get("required", [])
            for c in ("extracted_at_confidence", "confidence"):
                if c in req and c not in out:
                    out[c] = 0.7
        return out
    if isinstance(obj, list):
        item_schema = (schema or {}).get("items") if isinstance(schema, dict) else None
        return [repair_payload(x, item_schema) for x in obj]
    return obj


class _BaseClient:
    def vlm_generate(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> str:
        raise NotImplementedError

    def vlm_json(
        self, image: bytes | str, prompt: str, schema: dict, *,
        max_tokens: int = 900, retries: int = 2, lenient: bool = True,
    ) -> dict[str, Any]:
        """Return a schema-conforming object. With ``lenient`` (default), a repair
        pass fixes common model slips (value in the wrong key, missing confidence,
        stray keys); if it still won't validate after retries, the repaired
        best-effort object is returned with ``_partial=True`` instead of raising -
        a document should never be lost to a formatting nit."""
        last: Exception | None = None
        best: dict[str, Any] | None = None
        hint = ""
        for attempt in range(retries + 1):
            raw = self.vlm_generate(
                image, prompt + hint, max_tokens=max_tokens, json_schema=schema
            )
            try:
                obj = extract_json(raw)
            except (MLError, json.JSONDecodeError) as exc:
                last = exc
                hint = "\n\nReturn ONLY one valid JSON object, nothing else."
                log.warning("vlm_json_parse_retry", attempt=attempt, error=str(exc)[:140])
                continue
            if lenient:
                obj = repair_payload(obj, schema)
            best = obj
            try:
                validate_schema(obj, schema)
                return obj
            except jsonschema.ValidationError as exc:
                last = exc
                hint = (
                    "\n\nYour previous answer failed schema validation: "
                    f"{str(exc).splitlines()[0][:160]}. Fix ONLY that and resend the full JSON."
                )
                log.warning("vlm_json_retry", attempt=attempt, error=str(exc).splitlines()[0][:160])
        if lenient and isinstance(best, dict):
            best["_partial"] = True
            log.warning("vlm_json_partial", error=str(last).splitlines()[0][:160] if last else None)
            return best
        raise MLError(f"vlm_json failed after {retries + 1} attempts: {last}")

    def healthz(self) -> dict[str, Any]:
        raise NotImplementedError


class HttpMLClient(_BaseClient):
    def __init__(self, base_url: str | None = None, timeout: float = 240.0) -> None:
        self.base_url = (base_url or settings.mlserve_url).rstrip("/")
        self._c = httpx.Client(base_url=self.base_url, timeout=timeout)

    def healthz(self) -> dict[str, Any]:
        r = self._c.get("/healthz")
        r.raise_for_status()
        return r.json()

    def vlm_generate(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> str:
        payload = {
            "image_b64": _b64(image),
            "prompt": prompt,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }
        try:
            r = self._c.post("/vlm/generate", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise MLError(f"mlserve request failed: {exc}") from exc
        return r.json()["text"]


class StubMLClient(_BaseClient):
    """Deterministic offline stand-in for tests / no-GPU runs.

    Infers doc_type from keywords in the prompt's embedded OCR hint (the caller
    passes a short text excerpt), and echoes a trivial transcription. Not used on
    the pod, where the real transformers backend runs.
    """

    def healthz(self) -> dict[str, Any]:
        return {"status": "ok", "backend": "stub", "model": "stub", "device": "cpu"}

    def vlm_generate(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> str:
        if json_schema and json_schema.get("$id") == "cdi:classification.v1":
            return json.dumps(_stub_classify(prompt))
        return "STUB TRANSCRIPTION\nline one\nline two"


def get_client() -> _BaseClient:
    if settings.mlserve_backend == "stub":
        return StubMLClient()
    return HttpMLClient()
