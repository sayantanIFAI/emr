"""End-to-end ingest against real Postgres + MinIO. Auto-skips without infra.

Run:  make up  &&  CDI_DATABASE_URL=... CDI_S3_ENDPOINT_URL=... pytest -m integration
"""
from __future__ import annotations

import pymupdf as fitz
import pytest

pytestmark = pytest.mark.integration


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    pg = doc.new_page(width=420, height=300)
    pg.insert_text((36, 60), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_ingest_creates_rows_and_pages(infra):
    from cdi_adapter import repo, storage
    from cdi_adapter.db import session_scope
    from cdi_adapter.ingest.service import ingest_bytes

    storage.ensure_bucket()
    raw = _pdf("City Care Hospital\nPatient: Test Only\nHbA1c 7.8 %")

    res = ingest_bytes(raw, filename="unit.pdf", source_channel="test")
    assert res.deduplicated is False
    assert res.page_count == 1
    assert res.status == "pages_rendered"

    # dedupe on re-ingest
    res2 = ingest_bytes(raw, filename="unit.pdf", source_channel="test")
    assert res2.deduplicated is True
    assert res2.document_id == res.document_id

    with session_scope() as sess:
        doc = repo.get_document(sess, res.document_id)
        assert doc is not None
        assert doc["status"] == "pages_rendered"
        pages = repo.list_document_pages(sess, res.document_id)
        assert len(pages) == 1
        key = storage.key_from_uri(pages[0]["image_uri"])
        assert storage.get_bytes(key)[:4] == b"\x89PNG"

        runs = sess.execute(
            __import__("sqlalchemy").text(
                "SELECT stage, status FROM pipeline_run WHERE document_id = :d ORDER BY started_at"
            ),
            {"d": res.document_id},
        ).all()
        stages = {r[0]: r[1] for r in runs}
        assert stages.get("ingest") == "ok"
