"""Several pictures -> ONE multi-page PDF (``ingest/assemble.py``), and that nothing is made worse."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from cdi_adapter.ingest import assemble, pages


def _photo(w: int, h: int, mode: str = "RGB") -> Image.Image:
    im = Image.new("RGB", (w, h), (235, 230, 220))
    d = ImageDraw.Draw(im)
    for y in range(0, h, 40):
        d.text((20, y), "Tab Metformin 500 mg 1-0-1   HbA1c 7.8 %", fill=(20, 20, 60))
    return im.convert(mode)


def _enc(im: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _render(pdf: bytes, dpi: int = 200) -> list[Image.Image]:
    return [Image.open(io.BytesIO(p)).convert("RGB") for p in pages.render_pdf_pngs(pdf, dpi)]


def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, (a.size, b.size)
    return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())


def _exif(orientation: int) -> Image.Exif:
    ex = Image.Exif()
    ex[0x0112] = orientation
    return ex


JPG = _enc(_photo(1200, 1700), "JPEG", quality=92)
PNG = _enc(_photo(1000, 1400), "PNG")
GRAY_JPG = _enc(_photo(900, 1200, "L"), "JPEG", quality=90)


def test_sniff_reads_the_bytes_not_the_name():
    assert assemble.sniff_upload_type(JPG) == "image/jpeg"
    assert assemble.sniff_upload_type(PNG) == "image/png"
    assert assemble.sniff_upload_type(b"%PDF-1.7 ...") == "application/pdf"
    assert assemble.sniff_upload_type(_enc(_photo(50, 50), "TIFF")) == "image/tiff"
    for junk in (b"PK\x03\x04docx", b"MZ\x90\x00exe", b"GIF89a", b"BM....", b"", b"plain text"):
        assert assemble.sniff_upload_type(junk) is None


@pytest.mark.parametrize("raw", [JPG, PNG, GRAY_JPG], ids=["jpeg-rgb", "png", "jpeg-gray"])
def test_a_picture_comes_back_pixel_for_pixel_after_assembly_and_rendering(raw):
    """Page size is chosen so that rendering at the page DPI returns the picture's own pixels."""
    (page,) = _render(assemble.assemble_images_pdf([raw]))
    ref = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    assert _mean_abs_diff(page, ref) == 0.0


def test_a_jpeg_is_embedded_byte_for_byte_never_recompressed():
    assert JPG in assemble.assemble_images_pdf([JPG])
    assert GRAY_JPG in assemble.assemble_images_pdf([GRAY_JPG])


@pytest.mark.parametrize("orientation,expected", [(3, (1200, 800)), (6, (800, 1200)), (8, (800, 1200))])
def test_an_exif_rotated_phone_photo_is_upright_and_still_not_recompressed(orientation, expected):
    raw = _enc(_photo(1200, 800), "JPEG", quality=92, exif=_exif(orientation))
    pdf = assemble.assemble_images_pdf([raw])
    assert raw in pdf                                  # rotation is carried by the page, not the pixels
    (page,) = _render(pdf)
    ref = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    assert page.size == expected == ref.size
    assert _mean_abs_diff(page, ref) == 0.0


def test_a_mirrored_jpeg_takes_the_decode_path_and_is_still_upright():
    raw = _enc(_photo(1200, 800), "JPEG", quality=92, exif=_exif(2))      # mirror: no PDF rotation
    pdf = assemble.assemble_images_pdf([raw])
    (page,) = _render(pdf)
    ref = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    assert page.size == ref.size and _mean_abs_diff(page, ref) == 0.0


def test_pages_come_out_in_the_order_given_and_each_at_its_own_size():
    pdf = assemble.assemble_images_pdf([JPG, PNG, GRAY_JPG])
    assert [p.size for p in _render(pdf)] == [(1200, 1700), (1000, 1400), (900, 1200)]
    assert [p.size for p in _render(assemble.assemble_images_pdf([PNG, GRAY_JPG, JPG]))] == [
        (1000, 1400), (900, 1200), (1200, 1700)]


def test_the_same_pictures_always_give_the_same_bytes_so_duplicate_detection_works():
    a = assemble.assemble_images_pdf([JPG, PNG])
    assert a == assemble.assemble_images_pdf([JPG, PNG])
    assert a != assemble.assemble_images_pdf([PNG, JPG])


def test_a_multi_frame_tiff_gives_one_page_per_frame():
    frames = [_photo(600, 800), _photo(500, 700), _photo(400, 600)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    assert [p.size for p in _render(assemble.assemble_images_pdf([buf.getvalue()]))] == [
        (600, 800), (500, 700), (400, 600)]


def test_a_transparent_png_is_laid_on_white_paper():
    rgba = Image.new("RGBA", (800, 600), (0, 0, 0, 0))                    # fully transparent
    (page,) = _render(assemble.assemble_images_pdf([_enc(rgba, "PNG")]))
    assert page.getpixel((10, 10)) == (255, 255, 255)


def test_a_cmyk_jpeg_is_decoded_rather_than_embedded_with_wrong_colours():
    raw = _enc(_photo(800, 600).convert("CMYK"), "JPEG")
    pdf = assemble.assemble_images_pdf([raw])
    assert raw not in pdf and _render(pdf)[0].size == (800, 600)


def test_page_size_follows_the_dpi_so_a_different_render_dpi_still_matches():
    pdf = assemble.assemble_images_pdf([PNG], dpi=300)
    assert [p.size for p in _render(pdf, 300)] == [(1000, 1400)]


def test_only_pictures_can_be_combined():
    for bad in (b"%PDF-1.4 x", b"PK\x03\x04", b"hello"):
        with pytest.raises(ValueError):
            assemble.assemble_images_pdf([JPG, bad])
    with pytest.raises(ValueError):
        assemble.assemble_images_pdf([])


def test_the_assembled_pdf_goes_through_the_normal_page_pipeline_as_one_document():
    """What ingest does next: ``render_pages`` -> one RenderedPage per picture, in order."""
    pdf = assemble.assemble_images_pdf([JPG, PNG])
    out = pages.render_pages(pdf, "application/pdf")
    assert [p.page_no for p in out] == [1, 2]
    assert [(p.width_px, p.height_px) for p in out] == [(1200, 1700), (1000, 1400)]
