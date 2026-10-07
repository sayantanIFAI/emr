"""Reading the handwriting lines at once (a batching model server) must not change what comes back."""
from __future__ import annotations

import io
import threading
import time

from PIL import Image

from cdi_adapter.config import settings
from cdi_adapter.recognition import engines as eng


def _png(n: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40 + n, 12), "white").save(buf, format="PNG")      # the width tells the crops apart
    return buf.getvalue()


class _Client:
    """Answers each crop with its width; the first crops are the slowest, so they finish last."""

    def __init__(self, fail_width=None):
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.fail_width = fail_width

    def vlm_generate_ex(self, png, prompt, max_tokens=48):
        w = Image.open(io.BytesIO(png)).size[0]
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(0.05 if w < 45 else 0.0)
        with self.lock:
            self.in_flight -= 1
        if w == self.fail_width:
            raise RuntimeError("model gateway timed out")
        return f"line-{w}", "Qwen/Qwen2.5-VL-7B-Instruct"


def _run(monkeypatch, workers, client):
    monkeypatch.setattr("cdi_adapter.ml.client.get_client", lambda: client)
    monkeypatch.setattr(settings, "qwen_line_concurrency", workers)
    return eng.QwenLineEngine().recognize([_png(i) for i in range(12)])


def test_the_answers_come_back_in_the_order_of_the_crops_whatever_finishes_first(monkeypatch):
    c = _Client()
    out = _run(monkeypatch, 8, c)
    assert [r.text for r in out] == [f"line-{40 + i}" for i in range(12)]
    assert c.peak > 1                                   # they really were read together


def test_concurrency_one_is_the_old_serial_behaviour(monkeypatch):
    c = _Client()
    out = _run(monkeypatch, 1, c)
    assert c.peak == 1 and [r.text for r in out] == [f"line-{40 + i}" for i in range(12)]


def test_one_failed_line_does_not_spoil_the_others(monkeypatch):
    out = _run(monkeypatch, 8, _Client(fail_width=45))
    assert out[5].text == "" and "timed out" in (out[5].error or "")
    assert [r.text for i, r in enumerate(out) if i != 5] == [f"line-{40 + i}" for i in range(12) if i != 5]
