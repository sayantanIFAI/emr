"""End-to-end ingest against real Postgres + an S3 object store. Auto-skips without infra.

Run:  make up  &&  CDI_DATABASE_URL=... CDI_S3_ENDPOINT_URL=... pytest -m integration
"""
from __future__ import annotations

import pytest

from cdi_adapter.ingest.pdfgen import make_text_pdf

pytestmark = pytest.mark.integration


def _pdf(text: str) -> bytes:
    return make_text_pdf([text.splitlines()], width=420, height=300, x=36, y_top=60, leading=15)


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


def test_a_document_held_for_rescan_is_processed_again_when_the_same_bytes_arrive(infra):
    """A hold must never be permanent: the same picture sent again is read by today's code, not answered
    from the old hold (a fixed check or a changed setting may now read it)."""
    import uuid

    from cdi_adapter import repo, storage
    from cdi_adapter.db import session_scope
    from cdi_adapter.ingest.service import ingest_bytes

    storage.ensure_bucket()
    raw = _pdf(f"City Care Hospital\nHeld then retried {uuid.uuid4()}\nHbA1c 7.8 %")      # new bytes every run
    first = ingest_bytes(raw, filename="held.pdf", source_channel="test")
    with session_scope() as sess:
        repo.set_document_status(sess, first.document_id, "quality_hold", error_detail="rescan: page 1: test")

    again = ingest_bytes(raw, filename="held.pdf", source_channel="test")
    assert again.deduplicated is False and again.document_id == first.document_id
    assert again.status == "pages_rendered" and again.page_count == 1
    with session_scope() as sess:
        assert repo.get_document(sess, first.document_id)["error_detail"] is None
        assert len(repo.list_document_pages(sess, first.document_id)) == 1                 # redone, not doubled

    # and a document that is NOT held is still deduplicated as before
    third = ingest_bytes(raw, filename="held.pdf", source_channel="test")
    assert third.deduplicated is True
