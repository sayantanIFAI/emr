"""Unit tests for ingest helpers that don't touch the DB/S3."""
from __future__ import annotations

import hashlib

from cdi_adapter.ingest import service


def test_guess_mime_from_magic():
    assert service._guess_mime(None, b"%PDF-1.7 ...") == "application/pdf"
    assert service._guess_mime(None, b"\x89PNG\r\n\x1a\n") == "image/png"
    assert service._guess_mime(None, b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_guess_mime_from_extension_fallback():
    assert service._guess_mime("scan.tiff", b"\x00\x01\x02") == "image/tiff"


def test_allowed_mime():
    assert service._allowed("image/png")
    assert service._allowed("application/pdf")
    assert not service._allowed("text/html")


def test_ext_for():
    assert service._ext_for("application/pdf", None) == ".pdf"
    assert service._ext_for("image/png", "x.PNG") == ".png"
    assert service._ext_for("application/octet-stream", None) == ".bin"


def test_sha256_is_content_addressed():
    raw = b"same-bytes"
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(b"same-bytes").hexdigest()
