"""Pictures for the upload screen: the page (click to enlarge) and the name line cut out of it (webapp/images.py)."""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from cdi_adapter.webapp import app as webapp
from cdi_adapter.webapp import images

DOC = str(uuid.uuid4())


def _img(w=900, h=1200, v=240):
    a = np.full((h, w, 3), v, np.uint8)
    cv2.putText(a, "For Mr Onkar Chowdhury", (120, 330), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3)
    return cv2.imencode(".png", a)[1].tobytes()


@pytest.fixture
def fake(monkeypatch):
    store = {"norm": _img(v=200), "src": _img(), "orig": b"JPEGBYTES"}

    @contextmanager
    def scope():
        yield object()

    page = {"id": "p1", "page_no": 1, "image_uri": "norm", "preproc": {"src_uri": "src"}}
    doc = {"id": DOC, "mime_type": "image/jpeg", "object_uri": "orig", "name_read": "Mr. Onkar Chowdhury", "patient_name": "Onkar Chowdhury"}
    blocks = [{"id": "b1", "text": "For Mr. Onkar Chowdhury, 67yrs", "bbox": [100, 290, 800, 350], "page_id": "p1"}]
    monkeypatch.setattr(images, "session_scope", scope)
    monkeypatch.setattr(images.repo, "get_document", lambda s, d: doc if str(d) == DOC else None)
    monkeypatch.setattr(images.repo, "list_document_pages", lambda s, d: [page])
    monkeypatch.setattr(images.repo, "list_ocr_blocks", lambda s, d: blocks)
    monkeypatch.setattr(images.storage, "key_from_uri", lambda u: u)
    monkeypatch.setattr(images.storage, "get_bytes", lambda k: store[k])
    return store


def test_the_page_is_the_colour_source_the_original_photo_or_a_thumbnail(fake):
    data, mime = images.page_image(DOC, 1, "page")
    assert mime == "image/png" and data == fake["src"]                                   # the colour page, not the grey copy
    assert images.page_image(DOC, 1, "original") == (b"JPEGBYTES", "image/jpeg")
    small, _ = images.page_image(DOC, 1, "page", width=300)
    assert cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR).shape[1] == 300


@pytest.mark.parametrize("args", [("not-a-uuid", 1, "page"), (DOC, 2, "page"), (DOC, 1, "bogus"), (str(uuid.uuid4()), 1, "page")])
def test_a_bad_request_for_a_picture_is_none(fake, args):
    assert images.page_image(*args) is None


def test_the_name_line_is_cut_out_enlarged_and_a_missing_one_is_none(fake, monkeypatch):
    png = images.name_crop(DOC)
    h, w = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).shape[:2]
    assert h < 250 and w > 800                                                          # the one line, enlarged 1.6x
    assert images.name_crop("nope") is None and images.name_crop(str(uuid.uuid4())) is None


def test_the_routes_serve_the_pictures_and_answer_404_for_the_rest(fake):
    c = TestClient(webapp.app)
    c.headers["Authorization"] = "Basic " + __import__("base64").b64encode(f"{webapp.settings.admin_user}:{webapp.settings.admin_password}".encode()).decode()
    r = c.get(f"/api/intake/page-image?document_id={DOC}&page=1&view=page&w=200")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "max-age" in r.headers["cache-control"]
    assert c.get(f"/api/intake/name-crop?document_id={DOC}").status_code == 200
    assert c.get("/api/intake/page-image?document_id=x").status_code == 404
    assert c.get("/api/intake/name-crop?document_id=x").status_code == 404
