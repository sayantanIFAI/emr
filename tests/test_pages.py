"""Unit tests for page rendering + normalization. No infra required."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from cdi_adapter.ingest import pages
from cdi_adapter.ingest.pdfgen import make_text_pdf


def _make_pdf(text: str = "Hello Clinic\nMetformin 500 mg BD", pages_n: int = 1) -> bytes:
    return make_text_pdf([text.splitlines()] * pages_n, width=400, height=300, x=40, y_top=60,
                         leading=17, font_size=14)


def _make_png(w: int = 300, h: int = 200) -> bytes:
    arr = (np.random.rand(h, w) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def test_render_pdf_single_page():
    out = pages.render_pages(_make_pdf(), "application/pdf")
    assert len(out) == 1
    p = out[0]
    assert p.page_no == 1
    assert p.width_px > 0 and p.height_px > 0
    assert p.png_bytes[:4] == b"\x89PNG"
    assert "clahe" in p.preproc["steps"]


def test_render_pdf_multi_page():
    out = pages.render_pages(_make_pdf(pages_n=3), "application/pdf")
    assert [p.page_no for p in out] == [1, 2, 3]


def test_render_image():
    out = pages.render_pages(_make_png(), "image/png")
    assert len(out) == 1
    assert out[0].png_bytes[:4] == b"\x89PNG"


def test_normalize_returns_grayscale_png_and_meta():
    png = _make_png()
    norm, meta = pages.normalize_image(png)
    assert norm[:4] == b"\x89PNG"
    assert "skew_deg" in meta and "steps" in meta
    img = Image.open(io.BytesIO(norm))
    assert img.mode in ("L", "I;16", "1")


def test_unsupported_mime_raises():
    with pytest.raises(ValueError):
        pages.render_pages(b"not a document", "application/msword")


def test_thumbnail_smaller():
    png = _make_png(1000, 800)
    thumb = pages.make_thumbnail(png, max_side=200)
    img = Image.open(io.BytesIO(thumb))
    assert max(img.size) <= 200
