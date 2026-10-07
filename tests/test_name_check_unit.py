"""The patient's name is read again from its own line and the readings are compared; it stays 'to confirm' (extract/service.py)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import resolve_llm as R
from cdi_adapter.extract import service as X


def _png(w=900, h=1200):
    img = np.full((h, w, 3), 245, np.uint8)
    ok, enc = cv2.imencode(".png", img)
    return enc.tobytes()


class Seq:
    """A model that answers the name question with these readings, in order."""
    def __init__(self, *names): self.names, self.calls = list(names), 0
    def vlm_json_ex(self, image, prompt, schema, **kw):
        n = self.names[self.calls % len(self.names)]
        self.calls += 1
        return {"name": n}, "m"


BLOCKS = [{"id": "b1", "text": "For Mr. Oukar Broadway, 67yrs", "bbox": [120, 300, 800, 360], "page_id": "p1"},
          {"id": "b2", "text": "T2DM 19y", "bbox": [120, 400, 500, 450], "page_id": "p1"}]


def test_the_name_line_is_cut_out_and_enlarged_three_times():
    crops = R.name_crops(_png(), BLOCKS, "Mr. Oukar Broadway")
    sizes = [cv2.imdecode(np.frombuffer(c, np.uint8), cv2.IMREAD_COLOR).shape[:2] for c in crops]
    assert len(sizes) == 3 and sizes[0][0] < 200 and sizes[2][1] > sizes[0][1] * 2          # the one line, at 1x / 1.6x / 2.4x


def test_readings_that_agree_on_another_name_replace_the_first_reading_and_all_readings_are_kept():
    payload = {"patient": {"name": "Mr. Oukar Broadway"}}
    X._check_the_name(Seq("Oukar Chowdhury", "Oukar Chowdury", "Oukar Chowdhury"), _png(), BLOCKS, payload)
    assert payload["patient"]["name"].startswith("Oukar Chowd")
    assert payload["_name_reads"][0] == "Mr. Oukar Broadway" and len(payload["_name_reads"]) == 4
    assert payload["_name_agreement"] == [3, 4]


def test_readings_that_disagree_leave_the_first_reading_and_say_so():
    payload = {"patient": {"name": "Mr. Oukar Broadway"}}
    X._check_the_name(Seq("Ravi Das", "Kiran Paul", "Asha Rao"), _png(), BLOCKS, payload)
    assert payload["patient"]["name"] == "Mr. Oukar Broadway" and payload["_name_agreement"][0] == 1


def test_a_failed_or_switched_off_reread_changes_nothing(monkeypatch):
    class Boom:
        def vlm_json_ex(self, *a, **k): raise RuntimeError("down")
    p = {"patient": {"name": "Asha Rao"}}
    X._check_the_name(Boom(), _png(), BLOCKS, p)
    assert p["patient"]["name"] == "Asha Rao" and p["_name_reads"] == ["Asha Rao"]
    monkeypatch.setattr(settings, "name_reread", False)
    q = {"patient": {"name": "Asha Rao"}}
    X._check_the_name(Seq("Other Name"), _png(), BLOCKS, q)
    assert q["patient"]["name"] == "Asha Rao"


def test_the_model_is_given_the_picture_the_setting_names(monkeypatch):
    asked = []
    monkeypatch.setattr(X.storage, "key_from_uri", lambda u: u)
    monkeypatch.setattr(X.storage, "get_bytes", lambda k: asked.append(k) or b"img")
    pg = {"image_uri": "s3://p/norm.png", "preproc": {"src_uri": "s3://p/src.png"}}
    monkeypatch.setattr(settings, "model_image", "normalized")
    X._page_image(pg)
    monkeypatch.setattr(settings, "model_image", "source")
    X._page_image(pg)
    X._page_image({"image_uri": "s3://p/norm.png", "preproc": None})              # no source copy kept: the normalized one
    assert asked == ["s3://p/norm.png", "s3://p/src.png", "s3://p/norm.png"]
