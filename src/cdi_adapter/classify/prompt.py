from __future__ import annotations

import json
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "classification.v1.json"
CLASSIFICATION_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

DOC_TYPES = CLASSIFICATION_SCHEMA["properties"]["doc_type"]["enum"]

_INSTRUCTIONS = f"""\
Classify this scanned medical document.

Decide:
- doc_type: one of {DOC_TYPES}
- specialty: the clinical specialty if evident (e.g. cardiology, orthopaedics,
  general_medicine, obgyn, paediatrics), else null
- is_handwritten: true if the clinically meaningful content is predominantly
  handwritten (a printed letterhead with handwritten body counts as handwritten)
- languages: every script/language you can see text in (en, bn, hi, ta, ...)
- page_spans: only if this scan bundles more than one distinct document; otherwise []
- confidence: 0..1, your calibrated certainty in doc_type
- rationale: <= 40 words

Report only what is visible. If unsure, use doc_type "other" with low confidence."""


OCR_BEGIN = "<<<OCR_SAMPLE>>>"
OCR_END = "<<<END_OCR_SAMPLE>>>"


def build_classification_prompt(page_hint: str | None = None, n_pages: int = 1) -> str:
    parts = [_INSTRUCTIONS, f"\nThis scan has {n_pages} page(s)."]
    if page_hint:
        parts.append(
            "\nOCR text sample (noisy, for disambiguation only):\n"
            f"{OCR_BEGIN}\n{page_hint.strip()[:1200]}\n{OCR_END}"
        )
    return "\n".join(parts)
