"""One interface to the CPU OCR engines: over HTTP (``CDI_OCRHOST_URL`` set) or in-process."""
from __future__ import annotations

import base64
from typing import Protocol

import httpx

from ..config import settings
from ..logging import get_logger
from ..ocr.rapid import OcrLine
from .engines import Reading, TrOCREngine

log = get_logger(__name__)


class OcrHost(Protocol):
    def rapid(self, png: bytes) -> list[OcrLine]: ...
    def trocr(self, crops_png: list[bytes]) -> list[Reading]: ...


class LocalOcrHost:
    def __init__(self) -> None:
        self._trocr = TrOCREngine()

    def rapid(self, png: bytes) -> list[OcrLine]:
        from ..ocr.rapid import run_rapidocr

        return run_rapidocr(png)

    def trocr(self, crops_png: list[bytes]) -> list[Reading]:
        return self._trocr.recognize(crops_png) if crops_png else []


class HttpOcrHost:
    def __init__(self, base_url: str) -> None:
        self._c = httpx.Client(base_url=base_url.rstrip("/"), timeout=settings.ocrhost_timeout_s)

    def rapid(self, png: bytes) -> list[OcrLine]:
        r = self._c.post("/ocr/rapid", json={"image_b64": base64.b64encode(png).decode()})
        r.raise_for_status()
        return [OcrLine(text=x["text"], bbox=list(x["bbox"]), conf=float(x["conf"]),
                        polygon=x.get("polygon") or []) for x in r.json()["lines"]]

    def trocr(self, crops_png: list[bytes]) -> list[Reading]:
        if not crops_png:
            return []
        try:
            r = self._c.post("/ocr/trocr", json={
                "images_b64": [base64.b64encode(b).decode() for b in crops_png]})
            r.raise_for_status()
            js = r.json()
        except httpx.HTTPError as exc:
            # the CPU host being down degrades to "single engine" (-> forced review),
            # never to a silent auto-accept
            log.error("ocrhost_trocr_failed", error=str(exc)[:200])
            return [Reading("trocr", settings.trocr_model_id, "", None,
                            error=f"ocrhost unreachable: {exc}") for _ in crops_png]
        return [Reading("trocr", js.get("model") or settings.trocr_model_id, x["text"],
                        x.get("conf"), x.get("token_confidences") or [], error=x.get("error"))
                for x in js["readings"]]


_host: OcrHost | None = None


def get_ocr_host() -> OcrHost:
    global _host
    if _host is None:
        _host = HttpOcrHost(settings.ocrhost_url) if settings.ocrhost_url else LocalOcrHost()
    return _host


def set_ocr_host(host: OcrHost | None) -> None:
    """Test hook."""
    global _host
    _host = host
