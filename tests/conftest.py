from __future__ import annotations

import os

import pytest


def _infra_available() -> bool:
    return bool(os.environ.get("CDI_DATABASE_URL")) and bool(os.environ.get("CDI_S3_ENDPOINT_URL"))


@pytest.fixture(autouse=True)
def _no_database_for_the_lab_mapping(monkeypatch):
    """The mapping table is the built-in seed in unit tests (no database); a test that wants the table turns it on."""
    from cdi_adapter.config import settings
    from cdi_adapter.extract import lab_mapping

    monkeypatch.setattr(settings, "lab_mapping_db", False)
    lab_mapping.invalidate()
    yield
    lab_mapping.invalidate()


@pytest.fixture(autouse=True)
def _no_ocr_orientation_vote_by_default(request, monkeypatch):
    """The page-orientation vote reads the page with OCR; tests of the geometry rules stay fast and exact without it. Only
    test_orient_ocr_unit turns it on (with a fake OCR host)."""
    if not request.module.__name__.endswith("test_orient_ocr_unit"):
        from cdi_adapter.config import settings

        monkeypatch.setattr(settings, "orient_ocr_check", False)
    yield


@pytest.fixture(autouse=True)
def _no_enlarging_by_default(request, monkeypatch):
    """Many tests pin the size of a normalised page to the size it was rendered at; enlarging a small picture (ingest/enhance)
    changes that on purpose. Only test_enhance_unit tests it, and turns it on itself."""
    if not request.module.__name__.endswith("test_enhance_unit"):
        from cdi_adapter.config import settings

        monkeypatch.setattr(settings, "enhance_enabled", False)
    yield


@pytest.fixture(autouse=True)
def _fresh_upload_rate_limit():
    """The send-rate window is per process: every test starts with an empty one."""
    from cdi_adapter.webapp import upload

    upload.LIMITER._hits.clear()
    yield


@pytest.fixture(autouse=True)
def _no_shared_idempotency_rows(request, monkeypatch):
    """A fixed Idempotency-Key must not leave a row in a shared database that the next run answers with an old job.
    Only the integration module that tests the durable key uses the real table (with random keys)."""
    if request.module.__name__.endswith("test_out_s3_integration"):
        yield
        return
    from cdi_adapter.webapp import jobs

    monkeypatch.setattr(jobs, "_durable_key", lambda key, jid: jid)
    yield


@pytest.fixture(scope="session")
def infra() -> None:
    if not _infra_available():
        pytest.skip("integration infra not configured (CDI_DATABASE_URL / CDI_S3_ENDPOINT_URL)")


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    if _infra_available():
        return
    skip = pytest.mark.skip(reason="integration infra not configured")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
