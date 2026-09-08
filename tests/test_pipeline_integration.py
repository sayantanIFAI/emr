"""S1->S2->S3 against real Postgres + MinIO, stub VLM backend, RapidOCR.

Auto-skips without infra or without the `ocr` extra installed.
Run:  CDI_MLSERVE_BACKEND=stub CDI_DATABASE_URL=... CDI_S3_ENDPOINT_URL=... pytest -m integration
"""
from __future__ import annotations

import os

import pymupdf as fitz
import pytest

pytestmark = pytest.mark.integration


def _lab_pdf() -> bytes:
    doc = fitz.open()
    pg = doc.new_page(width=600, height=400)
    pg.insert_text(
        (40, 60),
        "CITY CARE HOSPITAL - BIOCHEMISTRY REPORT\n"
        "Patient: Test Only   MRN: LG-1\n"
        "TEST            RESULT   UNIT     REF. RANGE   FLAG\n"
        "HbA1c           7.8      %        4.0 - 5.6     H\n"
        "Creatinine      0.9      mg/dL    0.7 - 1.3     N\n",
        fontsize=11,
    )
    data = doc.tobytes()
    doc.close()
    return data


def test_ingest_classify_ocr(infra):
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
