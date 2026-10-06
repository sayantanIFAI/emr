"""S1->S2->S3 against real Postgres + an S3 object store, stub VLM backend, RapidOCR.

Auto-skips without infra or without the `ocr` extra installed.
Run:  CDI_MLSERVE_BACKEND=stub CDI_DATABASE_URL=... CDI_S3_ENDPOINT_URL=... pytest -m integration
"""
from __future__ import annotations

import os

import pytest

from cdi_adapter.ingest.pdfgen import make_text_pdf

pytestmark = pytest.mark.integration


def _lab_pdf() -> bytes:
    lines = [
        "CITY CARE HOSPITAL - BIOCHEMISTRY REPORT",
        "Patient: Test Only   MRN: LG-1",
        "TEST            RESULT   UNIT     REF. RANGE   FLAG",
        "HbA1c           7.8      %        4.0 - 5.6     H",
        "Creatinine      0.9      mg/dL    0.7 - 1.3     N",
    ]
    return make_text_pdf([lines], width=600, height=400, x=40, y_top=60, leading=14, font_size=11)


def test_ingest_classify_ocr(infra, monkeypatch):
    from cdi_adapter.config import settings

    monkeypatch.setattr(settings, "recognition_v2", False)     # this test covers the legacy page-level OCR path
    os.environ.setdefault("CDI_MLSERVE_BACKEND", "stub")
    pytest.importorskip("rapidocr_onnxruntime", reason="pip install .[ocr]")

    from cdi_adapter import repo, storage
    from cdi_adapter.classify.service import classify_document
    from cdi_adapter.db import session_scope
    from cdi_adapter.ingest.service import ingest_bytes
    from cdi_adapter.ocr.service import ocr_document

    storage.ensure_bucket()
    res = ingest_bytes(_lab_pdf(), filename="lab.pdf", source_channel="test")

    c = classify_document(res.document_id)
    assert c.doc_type == "lab_report"

    o = ocr_document(res.document_id)
    assert o.engine == "rapidocr"
    assert o.n_blocks >= 3

    with session_scope() as sess:
        cls = repo.get_doc_classification(sess, res.document_id)
        assert cls and cls["doc_type"] == "lab_report"
        blocks = repo.list_ocr_blocks(sess, res.document_id)
        assert any("hba1c" in b["text"].lower() for b in blocks)
        assert all(len(b["bbox"]) == 4 for b in blocks)
        doc = repo.get_document(sess, res.document_id)
        assert doc["status"] == "ocr_done"
        runs = sess.execute(
            __import__("sqlalchemy").text(
                "SELECT stage,status FROM pipeline_run WHERE document_id=:d"
            ),
            {"d": res.document_id},
        ).all()
        seen = {s: st for s, st in runs}
        assert seen.get("classify") == "ok"
        assert seen.get("ocr") == "ok"
