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

# long, list-heavy documents need a bigger output budget so every item is captured
MAX_TOKENS_BY_DOC_TYPE = {
    "prescription": 2600,
    "opd_note": 2400,
    "referral": 2400,
    "discharge_summary": 2600,
    "operative_note": 2400,
    "lab_report": 2000,
    "radiology_report": 2400,
    "vitals_sheet": 1400,
}

# extra, doc-type-specific guidance appended to the base prompt
_EXTRA = {
    "prescription": (
        "\nThis is a PRESCRIPTION. List EVERY medication line in `medications` - a "
        "typical prescription has 5-15 drugs. For each: `drug_text` = the full drug "
        "name as printed (brand or generic), `strength` = the numeric strength if "
        "shown, `frequency_text` = the timing/frequency notation verbatim - it MUST "
        "begin with the dose-slot pattern EXACTLY as printed if one is shown (e.g. "
        "'1-0-1', '0-0-1', '1-0-0'), then any words ('1-0-1 After Food Daily', "
        "'0-1-0 Before Food', 'BD x5days', 'TWICE IN A YEAR'). The dose-slot pattern "
        "sits in its own column next to the drug - never drop it. `duration` = the "
        "'x N days' / 'x 1 month' text. `instructions` = any 'Notes'/'Composition' "
        "text. Do NOT stop after the first few - include "
        "the last drug on the page. Put diagnoses in `diagnoses`, BP/weight in `vitals`."
    ),
    "lab_report": (
        "\nThis is a LAB REPORT. Put every analyte row in `results` with its numeric "
        "`value`, `unit`, reference range and flag. `value` is an object "
        "{value, unit_text, evidence}."
    ),
    "radiology_report": (
        "\nThis is an IMAGING / PROCEDURE report (e.g. USG, CT, MRI, ECHO, "
        "ANGIOGRAM, ENDOSCOPY). Copy the WHOLE `findings` section verbatim; also "
        "split it into `findings_list` (one item per finding, e.g. 'LAD 100% ISR', "
        "'LCX proximally 90% lesion'). Copy `impression` verbatim, set "
        "`procedure_name` if a procedure was done, and list any stated diagnosis "
        "in `diagnoses`. Do not summarise - capture every line."
    ),
    "discharge_summary": (
        "\nThis is a DISCHARGE SUMMARY. Capture every discharge diagnosis, every "
        "procedure with its date, and every discharge medication with dose and "
        "frequency. Put the hospital course narrative in `course_summary`."
    ),
}


def max_tokens_for(doc_type: str) -> int:
    from ..config import settings

    return MAX_TOKENS_BY_DOC_TYPE.get(doc_type, settings.extract_max_tokens)


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
    lines = [f"[b{i}] {b['text']}" for i, b in enumerate(ocr_blocks, start=1)]
    return (_BASE.format(doc_type=doc_type, ocr="\n".join(lines) or "(none)")
            + _EXTRA.get(doc_type, ""))


def block_id_map(ocr_blocks: list[dict[str, Any]]) -> dict[str, str]:
    """'b1' -> ocr_block uuid, in the same order used by build_extraction_prompt."""
    return {f"b{i}": str(b["id"]) for i, b in enumerate(ocr_blocks, start=1)}
