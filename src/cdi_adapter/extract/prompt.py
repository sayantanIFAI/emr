from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"

# doc_type -> extraction schema file
SCHEMA_FOR_DOC_TYPE = {
    "prescription": "prescription.v3.json",
    "lab_report": "lab_report.v3.json",
    "vitals_sheet": "vitals.v3.json",
    "opd_note": "opd_note.v3.json",
    "referral": "opd_note.v3.json",
    "discharge_summary": "discharge_summary.v3.json",
    "operative_note": "discharge_summary.v3.json",
    "radiology_report": "radiology.v3.json",
}

_cache: dict[str, dict[str, Any]] = {}


def load_schema(doc_type: str) -> tuple[str, dict[str, Any]] | None:
    fname = SCHEMA_FOR_DOC_TYPE.get(doc_type)
    if not fname:
        return None
    if fname not in _cache:
        _cache[fname] = json.loads((_SCHEMA_DIR / fname).read_text(encoding="utf-8"))
    return _cache[fname]["$id"], _cache[fname]


_BASE = """\
Extract structured clinical data from this scanned {doc_type}.

Rules:
- Fill the target JSON Schema EXACTLY. Use null / empty arrays when something is absent.
- For every object shaped like {{"text", "system", "code", "evidence"}}: put the
  human-readable clinical phrase in "text"; leave "system" and "code" null (a later
  step assigns standard codes). Never put the phrase in "code".
- Always include "extracted_at_confidence" (0..1) at the top level.
- Copy numbers, units, drug names and dosing notation EXACTLY as written.
- Every non-null value you emit MUST carry an "evidence" array of OCR block ids
  (like "b12"). If nothing supports a value, omit it.
- Do NOT infer, expand abbreviations, or add clinical judgement.
- Output ONLY the JSON object - no markdown fence, no commentary.

OCR blocks (id, text) - noisy, use together with the image:
{ocr}
"""


def build_extraction_prompt(doc_type: str, ocr_blocks: list[dict[str, Any]]) -> str:
    lines = []
    for i, b in enumerate(ocr_blocks, start=1):
        lines.append(f"[b{i}] {b['text']}")
    return _BASE.format(doc_type=doc_type, ocr="\n".join(lines) or "(none)")


def block_id_map(ocr_blocks: list[dict[str, Any]]) -> dict[str, str]:
    """'b1' -> ocr_block uuid, in the same order used by build_extraction_prompt."""
    return {f"b{i}": str(b["id"]) for i, b in enumerate(ocr_blocks, start=1)}
