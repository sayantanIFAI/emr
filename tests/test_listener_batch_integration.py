"""Listener batching + lifecycle against a real Postgres (CDI_DATABASE_URL), pipeline faked.

* files are picked THREE at a time and the three really run concurrently (a 3-party barrier
  inside the fake pipeline would time out if they were run one after another)
* success/ error/ log/ folders, retries <= 3, and failure reasons only in log/"""
from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.environ.get("CDI_DATABASE_URL"),
                                reason="CDI_DATABASE_URL not set")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.listener.connectors import LocalConnector

    monkeypatch.setattr(settings, "listener_stable_polls", 1)
    monkeypatch.setattr(settings, "listener_batch_size", 3)
    monkeypatch.setattr(settings, "listener_batch_wait_seconds", 3600)
    monkeypatch.setattr(settings, "listener_retry_base_seconds", 0)
    c = LocalConnector(str(tmp_path))
    c.ensure_folders()
    return c


def _drop(root, n: int, tag: str, prefix: str = "rx") -> list[str]:
    names = []
    for i in range(n):
        name = f"{prefix}{i}-{tag}.png"
        (root / "inbox" / name).write_bytes(f"png-{name}".encode())
        names.append(name)
    return names


def test_three_files_run_together_as_one_batch(conn, tmp_path, monkeypatch):
    from cdi_adapter.listener import service

    tag = uuid.uuid4().hex[:8]
    barrier = threading.Barrier(3, timeout=10)
    seen_threads: set[str] = set()

    def pipeline(raw, name, source_channel="listener"):
        seen_threads.add(threading.current_thread().name)
        barrier.wait()                       # only passes if all 3 are inside at the same moment
        return {"document_id": None, "facts": 1}

    monkeypatch.setattr(service, "run_pipeline", pipeline)
    names = _drop(tmp_path, 7, tag)

    assert service.poll_once(conn) == []                           # first sighting: not stable yet
    assert service.poll_once(conn) == ["completed"] * 3            # batch 1: exactly 3
    assert len(seen_threads) == 3                                  # on 3 different worker threads
    assert len(list((tmp_path / "success").glob(f"*-{tag}.png"))) == 3

    barrier.reset()
    assert service.poll_once(conn) == ["completed"] * 3            # batch 2: next 3
    assert len(list((tmp_path / "success").glob(f"*-{tag}.png"))) == 6

    barrier2 = threading.Barrier(1)
    monkeypatch.setattr(service, "run_pipeline",
                        lambda raw, name, source_channel="listener": barrier2.wait() or {"document_id": None})
    assert service.poll_once(conn) == []                           # 1 left: waits for company
    assert len(list((tmp_path / "inbox").glob(f"*-{tag}.png"))) == 1
    assert service.poll_once(conn, flush=True) == ["completed"]    # flushed (or batch_wait elapsed)
    assert len(list((tmp_path / "success").glob(f"*-{tag}.png"))) == 7
    assert not list((tmp_path / "log").iterdir())                  # nothing failed: log/ stays empty
    assert len(names) == 7


def test_failure_in_a_batch_does_not_hurt_the_others_and_retries_stop_at_three(conn, tmp_path, monkeypatch):
    from cdi_adapter.listener import recovery, service

    tag = uuid.uuid4().hex[:8]

    def pipeline(raw, name, source_channel="listener"):
        if name.startswith("bad"):
            raise ConnectionError("model gateway timeout")
        return {"document_id": None, "facts": 2}

    monkeypatch.setattr(service, "run_pipeline", pipeline)
    _drop(tmp_path, 2, tag, prefix="ok")
    _drop(tmp_path, 1, tag, prefix="bad")

    assert service.poll_once(conn) == []                           # first sighting: not stable yet
    assert sorted(service.poll_once(conn)) == ["completed", "completed", "error"]
    assert len(list((tmp_path / "success").glob(f"ok*-{tag}.png"))) == 2
    assert (tmp_path / "error" / f"bad0-{tag}.png").exists()
    assert not list((tmp_path / "error").glob("*.txt")) and not list((tmp_path / "error").glob("*.log"))
    assert len(list((tmp_path / "log").glob(f"bad0-{tag}.png.*.run1.log"))) == 1

    results = []
    for _ in range(3):
        results += [r["result"] for r in recovery.run_once(conn)]
    assert results == ["error", "error", "quarantine"]              # 3 retries, then parked
    assert recovery.run_once(conn) == []                            # never a 4th
    assert (tmp_path / "quarantine" / f"bad0-{tag}.png").exists()
    logs = sorted(p.name for p in (tmp_path / "log").glob(f"bad0-{tag}.png.*.log"))
    assert [n.rsplit(".", 2)[-2] for n in logs] == ["run1", "run2", "run3", "run4"]   # 1 run + 3 retries
    assert not list((tmp_path / "log").glob("ok*"))                # only failures are logged
    last = (tmp_path / "log" / logs[-1]).read_text()
    assert "retries exhausted" in last and "run:           4 of 4" in last

    with __import__("cdi_adapter.db", fromlist=["session_scope"]).session_scope() as s:
        row = s.execute(text("SELECT attempts, state FROM listener_file WHERE name=:n"),
                        {"n": f"bad0-{tag}.png"}).one()
    assert tuple(row) == (3, "quarantine")


def test_recovery_only_touches_the_active_connectors_files(conn, tmp_path, monkeypatch):
    """Switch OneDrive -> Google Drive by config: old OneDrive rows must be left alone."""
    from cdi_adapter.db import session_scope
    from cdi_adapter.listener import recovery

    tag = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.execute(text(
            "INSERT INTO listener_file (connector, remote_id, name, etag, state, attempts, error_class, "
            "next_attempt_at, stable_polls) VALUES ('onedrive', :r, :n, 'e1', 'error', 0, 'transient', "
            "now() - interval '1 hour', 1)"), {"r": f"od-{tag}", "n": f"old-{tag}.pdf"})
    assert recovery.run_once(conn) == []                          # 'local' recovery ignores 'onedrive'
    with session_scope() as s:
        st = s.execute(text("SELECT state, attempts FROM listener_file WHERE remote_id=:r"),
                       {"r": f"od-{tag}"}).one()
        s.execute(text("DELETE FROM listener_file WHERE remote_id=:r"), {"r": f"od-{tag}"})
    assert tuple(st) == ("error", 0)                              # untouched: not quarantined as 'missing'
