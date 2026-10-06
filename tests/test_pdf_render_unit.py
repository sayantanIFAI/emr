"""PDF rendering with pypdfium2 (``pages.render_pdf_pngs`` / ``pages.render_pages``), image
orientation, and the synthetic PDF writer.

Everything here is a unit test: no database, no object store. The PDFs are written by
``cdi_adapter.ingest.pdfgen`` or hand-assembled below; the password-protected one is a small
fixture (RC4-128, user password ``secret``).
"""

from __future__ import annotations

import base64
import io
import threading

import pypdfium2 as pdfium
import pytest
from PIL import Image

from cdi_adapter.ingest import pages
from cdi_adapter.ingest.pdfgen import assemble_pdf, make_text_pdf, stream_object

ENCRYPTED_PDF = base64.b64decode(
    "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgPDJhYzZmYzAyMzk+Cj4+CmVuZG9iagoyIDAgb2Jq"
    "Cjw8Ci9UeXBlIC9QYWdlcwovQ291bnQgMQovS2lkcyBbIDQgMCBSIF0KPj4KZW5kb2JqCjMgMCBvYmoKPDwKL1R5"
    "cGUgL0NhdGFsb2cKL1BhZ2VzIDIgMCBSCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9UeXBlIC9QYWdlCi9SZXNvdXJj"
    "ZXMgPDwKPj4KL01lZGlhQm94IFsgMC4wIDAuMCAyMDAgMjAwIF0KL1BhcmVudCAyIDAgUgo+PgplbmRvYmoKNSAw"
    "IG9iago8PAovViAyCi9SIDMKL0xlbmd0aCAxMjgKL1AgNDI5NDk2NzI5MgovRmlsdGVyIC9TdGFuZGFyZAovTyA8"
    "MGRiNTg1NWZjNTMyNjU2OWU3NjU5MDZjYWY2NGU0NDI5YTRjMjBkNmU5OTZmZGVmOTYzZTliNTA4MGY5ZTA4Mz4K"
    "L1UgPGI1YzFlM2U1NjVmZWUzNmM2YzNmYTNkMjkxNjdmNzVjMjhiZjRlNWU0ZTc1OGE0MTY0MDA0ZTU2ZmZmYTAx"
    "MDg+Cj4+CmVuZG9iagp4cmVmCjAgNgowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAw"
    "MDAwMDA1OSAwMDAwMCBuIAowMDAwMDAwMTE4IDAwMDAwIG4gCjAwMDAwMDAxNjcgMDAwMDAgbiAKMDAwMDAwMDI2"
    "MSAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDYKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKL0lEIFsgPDM1Mzk2"
    "MzMyMzA2MjYyNjE2NTYzMzgzMjY1MzE2MjM1NjMzNjMzNjM2MTYyNjU2NjM1NjE2NjYxNjU2NjMxMzE+IDwzNTM5"
    "NjMzMjMwNjI2MjYxNjU2MzM4MzI2NTMxNjIzNTYzMzYzMzYzNjE2MjY1NjYzNTYxNjY2MTY1NjYzMTMxPiBdCi9F"
    "bmNyeXB0IDUgMCBSCj4+CnN0YXJ0eHJlZgo0NzYKJSVFT0YK"
)


def make_pdf(pages_lines: list[list[str]]) -> bytes:
    return make_text_pdf(pages_lines)


def _size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as im:
        return im.size


def _render(pdf: bytes, dpi: int = 72) -> list[bytes]:
    """The raw page PNGs, before OpenCV normalisation (what pdfium produced)."""
    return pages.render_pdf_pngs(pdf, dpi)


def _dark_pixels(png: bytes, box: tuple[int, int, int, int] | None = None) -> int:
    with Image.open(io.BytesIO(png)) as im:
        gray = im.convert("L")
        if box:
            gray = gray.crop(box)
        return sum(gray.histogram()[:128])


def _raw_pdf(*page_bodies: bytes) -> bytes:
    """A blank PDF with one page per body (``/Type /Page /Parent`` is added)."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for body in page_bodies:
        kids.append(b"%d 0 R" % (len(objects) + 1))
        objects.append(b"<< /Type /Page /Parent 2 0 R " + body + b" >>")
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    return assemble_pdf(objects)


# ---------------------------------------------------------------- size, DPI, content


@pytest.mark.parametrize(
    ("dpi", "expected"),
    [(72, (595, 842)), (144, (1190, 1684)), (200, (1653, 2339)), (300, (2480, 3509))],
)
def test_page_size_is_ceil_of_points_times_dpi_over_72(dpi, expected):
    out = _render(make_pdf([["x"]]), dpi)
    assert _size(out[0]) == expected


@pytest.mark.parametrize(("dpi", "expected"), [(150, (1275, 1650)), (300, (2550, 3300))])
def test_exact_integer_sizes_get_no_extra_pixel(dpi, expected):
    """US Letter is 612 x 792 pt: at 150 and 300 dpi the pixel size is an exact integer."""
    pdf = _raw_pdf(b"/MediaBox [0 0 612 792]")
    assert _size(_render(pdf, dpi)[0]) == expected


def test_default_dpi_comes_from_settings(monkeypatch):
    monkeypatch.setattr(pages.settings, "page_dpi", 100)
    out = pages.render_pages(make_pdf([["x"]]), "application/pdf")
    assert (out[0].width_px, out[0].height_px) == (827, 1170)  # ceil(595 * 100/72), ceil(842 * 100/72)
    assert out[0].dpi == 100


def test_render_pages_returns_normalised_pages_with_the_colour_source():
    out = pages.render_pages(make_pdf([["HbA1c 7.8 %"], ["page two"]]), "application/pdf", dpi=100)
    assert [p.page_no for p in out] == [1, 2]
    for p in out:
        assert p.preproc["source"] == "pdf" and "clahe" in p.preproc["steps"]
        assert p.src_png and _size(p.src_png) == _size(p.png_bytes)  # same coordinates
        with Image.open(io.BytesIO(p.src_png)) as im:
            assert im.mode == "RGB"


def test_a_pdf_over_the_page_limit_is_refused_not_cut_short(monkeypatch):
    monkeypatch.setattr(pages.settings, "max_pages", 2)
    assert len(_render(make_pdf([["a"], ["b"]]))) == 2                  # at the limit: read
    with pytest.raises(pages.PdfReadError, match="3 pages; the limit is 2"):
        _render(make_pdf([["a"], ["b"], ["c"]]))                        # over it: nothing is read


def test_a_multipage_tiff_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(pages.settings, "max_pages", 2)
    frames = [Image.new("RGB", (700, 900), "white") for _ in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(pages.PdfReadError, match="3 pages"):
        pages.render_pages(buf.getvalue(), "image/tiff")


def test_rendered_text_is_visible_on_a_white_page():
    out = _render(make_pdf([["HbA1c 7.8 %", "Creatinine 1.1 mg/dL"]]), 150)
    with Image.open(io.BytesIO(out[0])) as im:
        assert im.mode == "RGB"
        assert im.getpixel((2, 2)) == (255, 255, 255)  # white background, never transparent/black
    assert _dark_pixels(out[0]) > 500


def test_blank_page_has_no_ink():
    out = _render(make_text_pdf([[]]), 100)
    assert _dark_pixels(out[0]) == 0


def test_text_lands_where_it_was_written():
    """The first baseline is 80 pt from the top: ink in that band, none in the bottom half."""
    out = _render(make_pdf([["WWWWWWWWWWWWWWWW"]]), 144)
    w, h = _size(out[0])
    band = (0, 2 * 80 - 24, w, 2 * 80 + 6)  # 144 dpi = 2 px per point
    assert _dark_pixels(out[0], band) > 200
    assert _dark_pixels(out[0], (0, h // 2, w, h)) == 0


def test_pages_come_out_in_order_with_their_own_text():
    out = _render(make_pdf([["A"], ["BBBBBBBBBBBBBBBBBBBB"], ["C"]]), 72)
    assert len(out) == 3
    ink = [_dark_pixels(p) for p in out]
    assert ink[1] > ink[0] and ink[1] > ink[2]


def test_mixed_page_sizes_are_each_rendered_at_their_own_size():
    pdf = _raw_pdf(b"/MediaBox [0 0 200 100]", b"/MediaBox [0 0 100 300]")
    out = _render(pdf, 72)
    assert [_size(p) for p in out] == [(200, 100), (100, 300)]


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_page_rotation_is_applied(rotate):
    out = _render(make_text_pdf([["rotated"]], rotate=rotate), 72)
    expected = (842, 595) if rotate in (90, 270) else (595, 842)
    assert _size(out[0]) == expected


def test_cropbox_limits_the_rendered_area():
    pdf = _raw_pdf(b"/MediaBox [0 0 600 800] /CropBox [100 100 400 300]")
    assert _size(_render(pdf, 72)[0]) == (300, 200)


def test_filled_form_field_is_drawn():
    objects = [
        (b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] /DA (/Helv 0 Tf 0 g) "
         b"/DR << /Font << /Helv 3 0 R >> >> >> >>"),
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Annots [5 0 R] "
         b"/Resources << /Font << /Helv 3 0 R >> >> >>"),
        (b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /V (FILLED) /Rect [20 100 280 160] "
         b"/F 4 /DA (/Helv 24 Tf 0 g) /AP << /N 6 0 R >> /P 4 0 R >>"),
        stream_object(
            b"/Tx BMC q BT /Helv 24 Tf 0 g 4 20 Td (FILLED) Tj ET Q EMC",
            b"/Type /XObject /Subtype /Form /BBox [0 0 260 60] "
            b"/Resources << /Font << /Helv 3 0 R >> >>",
        ),
    ]
    out = _render(assemble_pdf(objects), 72)
    assert _dark_pixels(out[0], (20, 40, 280, 100)) > 50  # the value sits inside the widget rect


# ---------------------------------------------------------------- bad input


def test_corrupt_pdf_raises_pdf_read_error():
    with pytest.raises(pages.PdfReadError, match="cannot open PDF"):
        pages.render_pages(b"%PDF-1.4 this is not really a pdf", "application/pdf")


def test_pdf_read_error_is_a_value_error():
    """Callers that already catch ValueError for bad input keep working."""
    assert issubclass(pages.PdfReadError, ValueError)


def test_listener_never_retries_an_unreadable_pdf():
    """A corrupt / locked / oversized PDF is the file's problem: no automatic retry."""
    from cdi_adapter.listener.service import _classify_error

    for message in (
        "cannot open PDF: Failed to load document (PDFium: Data format error)",
        "PDF is password-protected",
        "PDF page 1 is too large to render (1,600,000,000 pixels at this DPI)",
        "PDF page 2 has no size",
    ):
        assert _classify_error(pages.PdfReadError(message)) == "data", message


def test_truncated_pdf_raises_pdf_read_error():
    good = make_pdf([["page one"], ["page two"]])
    with pytest.raises(pages.PdfReadError):
        pages.render_pages(good[: len(good) // 3], "application/pdf")


def test_empty_bytes_with_pdf_mime_raises_pdf_read_error():
    with pytest.raises(pages.PdfReadError):
        pages.render_pages(b"", "application/pdf")


def test_password_protected_pdf_is_refused_with_a_clear_message():
    assert ENCRYPTED_PDF.startswith(b"%PDF") and b"/Encrypt" in ENCRYPTED_PDF
    with pytest.raises(pages.PdfReadError, match="password-protected"):
        pages.render_pages(ENCRYPTED_PDF, "application/pdf")


def test_zero_page_pdf_renders_no_pages():
    try:
        assert pages.render_pages(make_text_pdf([]), "application/pdf") == []
    except pages.PdfReadError:
        pass  # PDFium may also refuse a page-less file: both outcomes are a data error


def test_oversized_page_is_refused_before_anything_is_allocated():
    pdf = _raw_pdf(b"/MediaBox [0 0 14400 14400]")  # 200 inches square: 1.6 gigapixels at 200 dpi
    with pytest.raises(pages.PdfReadError, match="too large to render"):
        _render(pdf, 200)


def test_page_pixel_limit_is_a_module_constant(monkeypatch):
    monkeypatch.setattr(pages, "MAX_PDF_PAGE_PIXELS", 1000)
    with pytest.raises(pages.PdfReadError, match="too large to render"):
        _render(make_pdf([["x"]]), 72)


def test_real_documents_fit_under_the_pixel_limit_even_at_the_maximum_dpi():
    a3_at_600_dpi = (842 * 600 / 72) * (1191 * 600 / 72)
    a0_at_200_dpi = (2384 * 200 / 72) * (3370 * 200 / 72)
    assert max(a3_at_600_dpi, a0_at_200_dpi) < pages.MAX_PDF_PAGE_PIXELS


def test_degenerate_media_box_still_gives_a_usable_page():
    """PDFium substitutes its default page size; either way the result is never zero-sized."""
    pdf = _raw_pdf(b"/MediaBox [0 0 0 0]")
    try:
        w, h = _size(_render(pdf, 72)[0])
    except pages.PdfReadError:
        return  # refusing it is equally acceptable
    assert w > 0 and h > 0


# ---------------------------------------------------------------- thread safety and clean-up


def test_concurrent_rendering_is_correct():
    pdf = make_pdf([[f"page {i} line {j}" for j in range(8)] for i in range(3)])
    reference = _render(pdf, 100)
    results: list[list[bytes]] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(4):
                results.append(_render(pdf, 100))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 32 and all(r == reference for r in results)


def test_every_pdfium_call_runs_under_the_lock(monkeypatch):
    """PDFium is not thread-safe: opening pages, rendering and closing hold the module lock."""
    seen: list[str] = []

    def guarded(name, original):
        def wrapper(*args, **kwargs):
            assert pages._PDFIUM_LOCK.locked(), f"{name} called without the PDFium lock"
            seen.append(name)
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(pdfium.PdfPage, "render", guarded("render", pdfium.PdfPage.render))
    monkeypatch.setattr(pdfium.PdfDocument, "close", guarded("close", pdfium.PdfDocument.close))
    monkeypatch.setattr(
        pdfium.PdfDocument, "__getitem__", guarded("page", pdfium.PdfDocument.__getitem__)
    )
    _render(make_pdf([["a"], ["b"]]), 72)
    assert seen.count("render") == 2 and seen.count("page") == 2 and seen.count("close") == 1


def test_document_is_closed_even_when_a_page_fails(monkeypatch):
    closed: list[bool] = []
    original_close = pdfium.PdfDocument.close

    def tracking_close(self):
        closed.append(True)
        return original_close(self)

    monkeypatch.setattr(pdfium.PdfDocument, "close", tracking_close)
    monkeypatch.setattr(pages, "MAX_PDF_PAGE_PIXELS", 10)
    with pytest.raises(pages.PdfReadError):
        _render(make_pdf([["a"]]), 72)
    assert closed == [True]


# ---------------------------------------------------------------- the synthetic PDF writer


def test_pdfgen_output_is_a_valid_pdf_with_a_text_layer():
    pdf = make_text_pdf([["Tab Metformin 500 mg 1-0-1", "HbA1c 7.8 % (H) \\ ok"], ["page two"]])
    doc = pdfium.PdfDocument(pdf)
    try:
        assert len(doc) == 2
        first = doc[0].get_textpage().get_text_bounded()
        assert "Tab Metformin 500 mg 1-0-1" in first
        assert "HbA1c 7.8 % (H) \\ ok" in first  # parentheses and backslash are escaped
        assert "page two" in doc[1].get_textpage().get_text_bounded()
        assert doc[0].get_size() == (595, 842)
    finally:
        doc.close()


def test_pdfgen_xref_offsets_point_at_their_objects():
    pdf = make_text_pdf([["x"]])
    startxref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    table = pdf[startxref:].splitlines()
    assert table[0] == b"xref"
    count = int(table[1].split()[1])
    for number in range(1, count):
        offset = int(table[2 + number].split()[0])
        assert pdf[offset:].startswith(b"%d 0 obj" % number)


def test_pdfgen_rejects_a_rotation_that_is_not_a_multiple_of_90():
    with pytest.raises(ValueError):
        make_text_pdf([["x"]], rotate=45)


# ---------------------------------------------------------------- images: Pillow, never PDFium


def _photo(orientation: int | None, fmt: str = "JPEG") -> bytes:
    """A 120 x 60 'photo': black block on the left half, tagged with an EXIF orientation."""
    im = Image.new("RGB", (120, 60), "white")
    im.paste((0, 0, 0), (0, 0, 60, 60))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    buf = io.BytesIO()
    im.save(buf, format=fmt, exif=exif)
    return buf.getvalue()


def test_exif_orientation_is_applied_to_phone_photos():
    """Orientation 6 = the camera was rotated: the stored pixels are sideways and only the tag
    knows. The OCR models never see the tag, so the page must be turned upright here."""
    out = pages.render_pages(_photo(6), "image/jpeg")
    assert (out[0].width_px, out[0].height_px) == (60, 120)  # 120 x 60 stored, upright is 60 x 120
    assert out[0].preproc["source"] == "image"


def test_untagged_or_upright_image_keeps_its_shape():
    for orientation in (None, 1):
        out = pages.render_pages(_photo(orientation), "image/jpeg")
        assert (out[0].width_px, out[0].height_px) == (120, 60)


def test_damaged_exif_does_not_fail_the_page(monkeypatch):
    def boom(_img):
        raise ValueError("bad exif")

    monkeypatch.setattr(pages.ImageOps, "exif_transpose", boom)
    out = pages.render_pages(_photo(6), "image/jpeg")
    assert (out[0].width_px, out[0].height_px) == (120, 60)


def test_images_never_touch_pdfium(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("an image went through PDFium")

    monkeypatch.setattr(pdfium, "PdfDocument", fail)
    out = pages.render_pages(_photo(None, "PNG"), "image/png")
    assert len(out) == 1


def test_multi_frame_tiff_gives_one_page_per_frame():
    frames = [Image.new("RGB", (80, 50), c) for c in ("white", "lightgray", "gray")]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    out = pages.render_pages(buf.getvalue(), "image/tiff")
    assert [p.page_no for p in out] == [1, 2, 3]
    assert all(p.preproc["source"] == "tiff" for p in out)


def test_intermediate_pngs_use_the_fast_compression_level():
    """Level 1 is several times faster than Pillow's default 6 and the result is thrown away
    after normalisation; the stored page images are written by OpenCV."""
    im = Image.effect_noise((600, 800), 40).convert("RGB")
    fast = pages._pil_to_png(im, pages.INTERMEDIATE_PNG_LEVEL)
    default = pages._pil_to_png(im)
    assert pages.INTERMEDIATE_PNG_LEVEL == 1
    assert Image.open(io.BytesIO(fast)).tobytes() == im.tobytes()  # still lossless
    assert len(fast) >= len(default)
