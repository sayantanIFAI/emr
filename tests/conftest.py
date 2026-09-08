from __future__ import annotations

import os

import pytest


def _infra_available() -> bool:
    return bool(os.environ.get("CDI_DATABASE_URL")) and bool(os.environ.get("CDI_S3_ENDPOINT_URL"))


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
