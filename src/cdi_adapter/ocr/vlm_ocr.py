from __future__ import annotations

from ..logging import get_logger
from ..ml.client import get_client
from .rapid import OcrLine

log = get_logger(__name__)

_TRANSCRIBE_PROMPT = """\
Transcribe every line of clinically relevant text in this document image, in
natural reading order. One physical line per output line. Preserve numbers,
units, drug names and dosing notation exactly as written. Do NOT interpret,
expand abbreviations, correct spelling, or add anything not visibly present.
If a token is unreadable, write it as [??]. Output plain text only - no JSON,
no commentary."""


def run_vlm_transcription(png_bytes: bytes, page_w: int, page_h: int) -> list[OcrLine]:
    client = get_client()
    text = client.vlm_generate(png_bytes, _TRANSCRIBE_PROMPT, max_tokens=1400)
    full_box = [0, 0, int(page_w), int(page_h)]
    lines: list[OcrLine] = []
    for i, raw in enumerate(text.splitlines()):
        s = raw.strip()
        if not s:
            continue
        # unknown per-line geometry from a VLM transcription; page-level box, modest conf
        lines.append(OcrLine(text=s, bbox=full_box, conf=0.55, polygon=[]))
    return lines
