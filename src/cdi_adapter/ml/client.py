from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import jsonschema
import threading
from pathlib import Path

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"

# the model's text exactly as it came back on the last ``vlm_json_ex`` call of this thread: the extract
# step keeps it beside the parsed object (OUT-S1); per thread, so concurrent documents never mix
_last = threading.local()


def last_raw_answer() -> str | None:
    return getattr(_last, "raw", None)


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
        # never quote the output: it is a reading of a patient's document, and this message
        # reaches logs, the listener's failure notes and error_detail
        raise MLError(f"no JSON object in model output ({len(text)} chars)")
    return json.loads(m.group(0))


def error_location(exc: jsonschema.ValidationError) -> str:
    """Where a schema check failed (``tests/0/name``), never what was there: a jsonschema message
    quotes the offending value (``['Anil Mehra'] is not of type 'string'``), which for a clinical
    document is patient data, and logs are shipped to a log store."""
    return "/".join(str(p) for p in exc.absolute_path)[:80] or "<root>"


def salvage_truncated(text: str, max_tries: int = 400) -> dict[str, Any] | None:
    """The complete part of an answer that was cut off by the length limit, or ``None``.

    Cuts at the last comma that sits outside a string and closes the open brackets, so only elements
    that were completely written are kept. The final element (which may itself be cut, such as a
    dose of 12 that was going to be 125) is always dropped, and nothing is ever completed or guessed.
    The caller marks the result incomplete."""
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    stack: list[str] = []
    in_str = esc = False
    cuts: list[tuple[int, str]] = []                  # (comma position, closers needed there)
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == "," and stack:
            cuts.append((i, "".join(reversed(stack))))
    for pos, closers in list(reversed(cuts))[:max_tries]:
        try:
            obj = json.loads(s[:pos] + closers)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


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


@dataclass
class GenResult:
    """One model answer with what is known about it."""

    text: str
    model: str | None = None
    truncated: bool = False          # the answer hit the length limit: it is cut off, not finished


class _BaseClient:
    def vlm_generate_full(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> GenResult:
        text, model = self.vlm_generate_ex(image, prompt, max_tokens=max_tokens, json_schema=json_schema)
        return GenResult(text, model)

    def vlm_generate(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> str:
        raise NotImplementedError

    def vlm_generate_ex(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> tuple[str, str | None]:
        """``(text, model that answered)``. The gateway names the model per response, so a request
        served by the OOM fallback model is recorded as such. A client that cannot say returns
        ``None``."""
        return self.vlm_generate(image, prompt, max_tokens=max_tokens, json_schema=json_schema), None

    def vlm_json(
        self, image: bytes | str, prompt: str, schema: dict, *,
        max_tokens: int = 900, retries: int = 2, lenient: bool = True,
    ) -> dict[str, Any]:
        return self.vlm_json_ex(image, prompt, schema, max_tokens=max_tokens, retries=retries,
                                lenient=lenient)[0]

    def vlm_json_ex(
        self, image: bytes | str, prompt: str, schema: dict, *,
        max_tokens: int = 900, retries: int = 2, lenient: bool = True,
    ) -> tuple[dict[str, Any], str | None]:
        """``(object, model that answered the last attempt)``: a schema-conforming object. With
        ``lenient`` (default), a repair
        pass fixes common model slips (value in the wrong key, missing confidence,
        stray keys); if it still won't validate after retries, the repaired
        best-effort object is returned with ``_partial=True`` instead of raising -
        a document should never be lost to a formatting nit."""
        last: Exception | None = None
        best: dict[str, Any] | None = None
        hint = ""
        served: str | None = None
        cut = marked = False
        for attempt in range(retries + 1):
            got = self.vlm_generate_full(image, prompt + hint, max_tokens=max_tokens, json_schema=schema)
            raw, served, cut = got.text, got.model, got.truncated
            _last.raw = raw
            if cut:
                log.warning("vlm_json_truncated", attempt=attempt, max_tokens=max_tokens)
            salvaged = False
            try:
                obj = extract_json(raw)
            except (MLError, json.JSONDecodeError) as exc:
                rescued = salvage_truncated(raw) if cut else None
                if rescued is None:
                    last = exc
                    hint = "\n\nReturn ONLY one valid JSON object, nothing else."
                    log.warning("vlm_json_parse_retry", attempt=attempt, error=str(exc)[:140])
                    continue
                obj, salvaged = rescued, True
                log.warning("vlm_json_salvaged", attempt=attempt)
            if lenient:
                obj = repair_payload(obj, schema)
            best = obj
            marked = cut or salvaged
            try:
                validate_schema(obj, schema)
                if marked:      # parsed, but the model hit the length limit: never present it as finished
                    obj["_partial"] = obj["_truncated"] = True
                return obj, served
            except jsonschema.ValidationError as exc:
                last = exc
                hint = (
                    "\n\nYour previous answer failed schema validation: "
                    f"{str(exc).splitlines()[0][:160]}. Fix ONLY that and resend the full JSON."
                )
                log.warning("vlm_json_retry", attempt=attempt, error_at=error_location(exc))
        if lenient and isinstance(best, dict):
            best["_partial"] = True
            if marked:
                best["_truncated"] = True
            log.warning("vlm_json_partial",
                        error_at=error_location(last) if isinstance(last, jsonschema.ValidationError)
                        else type(last).__name__)
            return best, served
        # a schema message quotes the offending value (patient data): say where, not what
        why = error_location(last) if isinstance(last, jsonschema.ValidationError) else str(last)
        raise MLError(f"vlm_json failed after {retries + 1} attempts: {why}")

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
        return self.vlm_generate_ex(image, prompt, max_tokens=max_tokens,
                                    json_schema=json_schema)[0]

    def vlm_generate_ex(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> tuple[str, str | None]:
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
        body = r.json()
        model = body.get("model")
        return body["text"], model if isinstance(model, str) and model else None

    def vlm_generate_full(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> GenResult:
        payload = {"image_b64": _b64(image), "prompt": prompt, "max_tokens": max_tokens,
                   "json_schema": json_schema}
        try:
            r = self._c.post("/vlm/generate", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise MLError(f"mlserve request failed: {exc}") from exc
        body = r.json()
        model = body.get("model")
        done = (body.get("usage") or {}).get("completion_tokens")
        return GenResult(body["text"], model if isinstance(model, str) and model else None,
                         isinstance(done, int) and done >= max_tokens)


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

    def vlm_generate_ex(
        self, image: bytes | str, prompt: str, *, max_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> tuple[str, str | None]:
        text = self.vlm_generate(image, prompt, max_tokens=max_tokens, json_schema=json_schema)
        return text, "stub"


def get_client() -> _BaseClient:
    if settings.mlserve_backend == "stub":
        return StubMLClient()
    return HttpMLClient()
