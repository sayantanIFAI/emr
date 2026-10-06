"""ENT-S4 against a real PostgreSQL: the metrics text, the health page, the listener numbers, the swap list."""
from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.environ.get("CDI_DATABASE_URL"), reason="CDI_DATABASE_URL not set")

_LINE = re.compile(r'^(# (HELP|TYPE) [a-z_:][a-z0-9_:]* .+|[a-z_:][a-z0-9_:]*(\{([a-z_]+="[^"]*",?)+\})? -?[0-9.eE+]+)$')


@pytest.fixture()
def client(monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.webapp import app as webapp

    monkeypatch.setattr(settings, "admin_password", "")
    return TestClient(webapp.app)


def test_metrics_are_valid_prometheus_text_with_the_numbers_an_alert_needs(client):
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    lines = [ln for ln in r.text.splitlines() if ln]
    bad = [ln for ln in lines if not _LINE.match(ln)]
    assert not bad, bad[:3]
    names = {ln.split("{")[0].split(" ")[0] for ln in lines if not ln.startswith("#")}
    for needed in ("cdi_up", "cdi_db_up", "cdi_object_store_up", "cdi_queue_up", "cdi_model_gateway_up", "cdi_ocr_host_up",
                   "cdi_documents_unfinished_oldest_seconds", "cdi_documents_last_hour", "cdi_stage_failures_24h"):
        assert needed in names, needed
    assert "cdi_db_up 1" in r.text


def test_a_down_part_reports_zero_it_does_not_disappear(client, monkeypatch):
    from cdi_adapter import storage

    monkeypatch.setattr(storage, "ping", lambda: False)
    assert "cdi_object_store_up 0" in client.get("/metrics").text


def test_the_listener_numbers_are_in_the_metrics(client):
    from cdi_adapter.db import session_scope

    with session_scope() as s:
        s.execute(text("INSERT INTO listener_heartbeat (connector, last_poll_at, last_poll_ok_at) VALUES ('local', now(), now()) "
                       "ON CONFLICT (connector) DO UPDATE SET last_poll_ok_at = now(), stopped_reason = NULL"))
    t = client.get("/metrics").text
    assert 'cdi_listener_files{state="waiting"}' in t and "cdi_listener_seconds_since_good_poll" in t
    assert "cdi_listener_stalled 0" in t


def test_the_status_page_and_swap_list_render_from_live_probes(client):
    page = client.get("/status")
    assert page.status_code == 200 and "System status" in page.text
    for word in ("Components", "File listener", "Models in use", "Swap points", "pypdfium2", "CDI_PDF_RENDERER"):
        assert word in page.text
    sp = client.get("/api/swap-points").json()["swap_points"]
    assert {s["swap_point"] for s in sp} >= {"pdf_renderer", "object_store", "job_queue"}


def test_metrics_and_status_need_the_sign_in(monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.webapp import app as webapp

    monkeypatch.setattr(settings, "admin_password", "a-long-admin-pass")
    c = TestClient(webapp.app)
    for p in ("/metrics", "/status", "/api/swap-points", "/api/listener/health"):
        assert c.get(p).status_code == 401
