"""Run the reading stages again on documents already in the database (after a code or setting change), then print a
short summary of each result. The same stage chain as an upload, without the dedupe shortcut.

    python scripts/rerun_docs.py photo1_apollo_clinic.jpg photo2_sugar_clinic.jpg
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from cdi_adapter.classify.service import classify_document
from cdi_adapter.config import settings
from cdi_adapter.db import session_scope
from cdi_adapter.extract.service import extract_document
from cdi_adapter.ocr.service import ocr_document
from cdi_adapter.output.json_connector import get_connector
from cdi_adapter.terminology.service import bind_document
from cdi_adapter.validate.service import validate_document


def main(names: list[str]) -> int:
    with session_scope() as s:
        ids = [(str(r[0]), r[1]) for n in names for r in s.execute(text(
            "SELECT id, original_filename FROM source_document WHERE original_filename = :n ORDER BY ingested_at DESC LIMIT 1"),
            {"n": n}).all()]
    for did, fn in ids:
        ocr_document(did, force_engine="rapidocr")
        c = classify_document(did)
        if settings.recognition_v2 or c.is_handwritten:
            ocr_document(did, force_engine="vlm")
        extract_document(did)
        bind_document(did)
        validate_document(did)
        r = get_connector().render(did)
        print("==", fn, r["status"], "needs_check", r["needs_check_count"])
        for sec in ("lab_tests", "medications", "advice"):
            for it in r[sec]:
                nm = it.get("as_written") or it.get("drug") or it.get("text")
                ref = f"  -> {it['reference_name']}" if it.get("reference_name") else ""
                print(f"  {sec[:5]:5} {it['status']:11} {str(nm)[:36]:36}{ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
