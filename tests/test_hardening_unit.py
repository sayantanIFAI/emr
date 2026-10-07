"""Hardening items from the security review: the signed-in user is the reviewer, the start-up checks run however the
app is started, and the container files stay safe."""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdi_adapter.config import settings
from cdi_adapter.webapp import app as webapp
from cdi_adapter.webapp import surface

ROOT = Path(__file__).resolve().parents[1]


def _auth(pw="a-long-admin-pass"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}


def test_the_signed_in_user_is_the_reviewer_whatever_the_body_says(monkeypatch):
    seen = {}
    monkeypatch.setattr(settings, "admin_password", "a-long-admin-pass")
    monkeypatch.setattr(settings, "review_ui_enabled", True)
    monkeypatch.setattr(webapp.review_svc, "submit_decision",
                        lambda fact, action, **kw: seen.update(kw) or {"ok": True})
    c = TestClient(webapp.app)
    r = c.post("/api/facts/f1/review", json={"action": "accept", "reviewer": "dr.someone.else"}, headers=_auth())
    assert r.status_code == 200 and seen["reviewer"] == "admin"            # the body's name is ignored


def test_with_no_sign_in_in_dev_the_claimed_name_is_used_and_defaults(monkeypatch):
    seen = {}
    monkeypatch.setattr(settings, "admin_password", "")
    monkeypatch.setattr(settings, "review_ui_enabled", True)
    monkeypatch.setattr(webapp.review_svc, "submit_decision", lambda fact, action, **kw: seen.update(kw) or {"ok": True})
    c = TestClient(webapp.app)
    c.post("/api/facts/f1/review", json={"action": "accept", "reviewer": "dr.rao"})
    assert seen["reviewer"] == "dr.rao"
    c.post("/api/facts/f1/review", json={"action": "accept"})
    assert seen["reviewer"] == "reviewer"


def test_reviewer_name_helper():
    class Req:
        state = type("S", (), {"user": "admin"})()

    class NoUser:
        state = type("S", (), {})()
    assert surface.reviewer_name(Req(), "x") == "admin"
    assert surface.reviewer_name(NoUser(), "x") == "x" and surface.reviewer_name(NoUser(), None) == "reviewer"


def test_the_startup_checks_run_when_the_app_is_started_by_uvicorn_too(monkeypatch):
    """`uvicorn cdi_adapter.webapp.app:app` never calls main(): the lifespan must run the same checks."""
    monkeypatch.setattr(settings, "env", "runpod")
    monkeypatch.setattr(settings, "admin_password", "")                        # not dev, no password: must refuse
    with pytest.raises(RuntimeError, match="CDI_ADMIN_PASSWORD"):
        with TestClient(webapp.app):
            pass


def test_the_lifespan_starts_cleanly_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "env", "dev")
    monkeypatch.setattr(settings, "resume_on_start", False)
    with TestClient(webapp.app) as c:
        assert c.get("/healthz").status_code in (200, 503)


def test_the_compose_file_publishes_nothing_to_the_network_and_runs_the_hardened_app():
    lines = (ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8").splitlines()
    text = chr(10).join(ln for ln in lines if not ln.lstrip().startswith("#"))
    ports = re.findall(r'ports:\s*\["([^"]+)"\]', text)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports), ports
    assert "cdi_adapter.api:app" not in text and "python -m cdi_adapter.webapp" in text
    assert "${CDI_ADMIN_PASSWORD:?" in text                                   # refuses to start without a password


def test_the_container_runs_as_a_normal_user_without_the_test_tools():
    d = (ROOT / "infra/compose/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER cdi", d, re.M) and "[dev" not in d and "cdi_adapter.api:app" not in d
