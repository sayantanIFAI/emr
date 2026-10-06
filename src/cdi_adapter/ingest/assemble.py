"""Several pictures of ONE prescription -> one multi-page PDF (UP-S1).

The upload screen lets a person add the pages of a single prescription (camera shots, scans,
files). Downstream stages already read a multi-page PDF as one document with N pages, so the
pictures are assembled into one here instead of being read as N unrelated documents.

Nothing about a picture is made worse:

* a JPEG (grey or RGB, upright or with a plain rotation tag) is embedded **byte for byte**
  (``DCTDecode``): no decode, no re-compression;
* anything else (PNG, TIFF frame, CMYK / mirrored JPEG, alpha) is decoded once and stored with
  lossless ``FlateDecode``; a transparent picture is composited on white (paper);
* each page is sized so that rendering at ``dpi`` gives back the picture's own pixels 1:1 (no
  resampling);
* the output is deterministic (no timestamps), so the same pictures give the same bytes and the
  same SHA-256, and ingest's duplicate check keeps working.

The caller keeps every original upload untouched next to the assembled file
(``webapp/jobs.py: store_parts``): the PDF is a derived container, never the only copy.
"""
from __future__ import annotations

import io
import zlib
from dataclasses import dataclass

from PIL import Image, ImageOps

from ..config import settings
from .pdfgen import assemble_pdf, stream_object

UPLOAD_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/tiff")
_EXIF_ORIENTATION = 0x0112
_ROTATE_FOR_EXIF = {3: 180, 6: 90, 8: 270}      # plain rotations a PDF page can carry itself


def sniff_upload_type(raw: bytes) -> str | None:
    """The upload's real type from its first bytes, or ``None`` if it is not one of
    :data:`UPLOAD_TYPES`. The file name is never trusted: a renamed file is not a picture."""
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    return None


@dataclass
class _Page:
    width_px: int
    height_px: int
    colorspace: str            # /DeviceGray | /DeviceRGB
    filter: str                # /DCTDecode | /FlateDecode
    data: bytes
    rotate: int = 0


def _flat(img: Image.Image) -> Image.Image:
    """Decoded, upright, opaque 8-bit grey or RGB."""
    img = ImageOps.exif_transpose(img) or img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        paper = Image.new("RGB", rgba.size, "white")
        paper.paste(rgba, mask=rgba.getchannel("A"))
        return paper
    return img.convert("L") if img.mode in ("L", "1") else img.convert("RGB")


def _decoded_page(img: Image.Image) -> _Page:
    flat = _flat(img)
    gray = flat.mode == "L"
    return _Page(flat.width, flat.height, "/DeviceGray" if gray else "/DeviceRGB",
                 "/FlateDecode", zlib.compress(flat.tobytes(), 6))


def _pages_of(raw: bytes) -> list[_Page]:
    mime = sniff_upload_type(raw)
    if mime not in ("image/png", "image/jpeg", "image/tiff"):
        raise ValueError("only pictures (JPG, PNG, TIFF) can be combined into one prescription")
    with Image.open(io.BytesIO(raw)) as img:
        if img.format == "JPEG" and img.mode in ("L", "RGB"):
            orient = int(img.getexif().get(_EXIF_ORIENTATION, 1) or 1)
            if orient == 1 or orient in _ROTATE_FOR_EXIF:
                return [_Page(img.width, img.height,
                              "/DeviceGray" if img.mode == "L" else "/DeviceRGB",
                              "/DCTDecode", raw, _ROTATE_FOR_EXIF.get(orient, 0))]
        frames = int(getattr(img, "n_frames", 1))
        out = []
        for i in range(frames):
            img.seek(i)
            out.append(_decoded_page(img.copy()))
        return out


def assemble_images_pdf(parts: list[bytes], *, dpi: int | None = None) -> bytes:
    """One PDF page per picture (a multi-frame TIFF gives one page per frame), in the order given."""
    dpi = dpi or settings.page_dpi
    pages: list[_Page] = []
    for raw in parts:
        pages += _pages_of(raw)
    if not pages:
        raise ValueError("no pictures to combine")
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids: list[int] = []
    for pg in pages:
        number = len(objects) + 1              # this page's object number
        kids.append(number)
        w_pt, h_pt = pg.width_px * 72.0 / dpi, pg.height_px * 72.0 / dpi
        content = b"q %.4f 0 0 %.4f 0 0 cm /Im0 Do Q" % (w_pt, h_pt)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.4f %.4f] /Rotate %d "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (w_pt, h_pt, pg.rotate, number + 2, number + 1))
        objects.append(stream_object(content))
        objects.append(stream_object(
            pg.data,
            b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
            b"/BitsPerComponent 8 /Filter %s"
            % (pg.width_px, pg.height_px, pg.colorspace.encode(), pg.filter.encode())))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    return assemble_pdf(objects)
