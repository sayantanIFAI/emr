"""Listener stories against a real PostgreSQL (migrations 0007-0009): a crash between the database save and
the move, the ignored-file record, the health view and stall rule, and the file-name lookup. Pipeline faked."""
from __future__ import annotations

import hashlib
import io
import os
import uuid

import pytest
from PIL import Image
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.environ.get("CDI_DATABASE_URL"), reason="CDI_DATABASE_URL not set")


class Killed(BaseException):
    """A process kill: not an Exception, so nothing in the listener catches it (like SIGKILL)."""


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.listener.connectors import LocalConnector

    monkeypatch.setattr(settings, "listener_stable_polls", 1)
    monkeypatch.setattr(settings, "listener_batch_size", 1)
    monkeypatch.setattr(settings, "listener_batch_wait_seconds", 0)
    monkeypatch.setattr(settings, "listener_retry_base_seconds", 0)
    c = LocalConnector(str(tmp_path))
    c.ensure_folders()
    return c


@pytest.fixture()
def fake_store(monkeypatch):
    from cdi_adapter import storage

    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "put_bytes", lambda k, d, content_type="": blobs.__setitem__(k, d) or f"s3://t/{k}")
    monkeypatch.setattr(storage, "object_uri", lambda k: f"s3://t/{k}")
    monkeypatch.setattr(storage, "get_bytes", lambda k: blobs[k])
    return blobs


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (900, 700), "white").save(b, "PNG")
    return b.getvalue() + uuid.uuid4().bytes


def _db():
    from cdi_adapter.db import session_scope

    return session_scope


def test_a_crash_between_the_save_and_the_move_is_recovered_without_saving_twice(conn, tmp_path, monkeypatch, fake_store):
    """AC3 of LS-S6: the result is in the database, the process dies before the file moves to success.
    On restart the lease expires, the file is moved to success, and there is still ONE document."""
    from cdi_adapter.ingest.service import ingest_bytes
    from cdi_adapter.listener import recovery, service

    monkeypatch.setattr("cdi_adapter.ingest.service._enqueue_next", lambda d: None)
    raw = _png()
    name = f"crash-{uuid.uuid4().hex[:8]}.png"
    (tmp_path / "inbox" / name).write_bytes(raw)
    runs: list[str] = []

    def pipeline(data, filename, source_channel="listener"):
        res = ingest_bytes(data, filename=filename, source_channel=source_channel)   # the "database save"
        with _db()() as s:
            s.execute(text("UPDATE source_document SET status='validated' WHERE id=:d"), {"d": res.document_id})
        runs.append(res.document_id)
        return {"document_id": res.document_id, "dedup": res.deduplicated}

    monkeypatch.setattr(service, "run_pipeline", pipeline)
    real_move = conn.move

    def dying_move(f, folder):
        if folder == "completed":
            raise Killed()                                   # dies after the save, before the move
        return real_move(f, folder)

    monkeypatch.setattr(conn, "move", dying_move)
    service.poll_once(conn)                                  # first sighting (stable_polls=1: ready at once)
    with pytest.raises(Killed):
        service.poll_once(conn)
    assert not list((tmp_path / "success").iterdir())        # never reached success
    with _db()() as s:
        row = s.execute(text("SELECT state, document_id FROM listener_file WHERE name=:n"), {"n": name}).one()
        s.execute(text("UPDATE listener_file SET lease_until = now() - interval '1 minute' WHERE name=:n"), {"n": name})
    assert row[0] == "processing"

    monkeypatch.setattr(conn, "move", real_move)             # the restarted process
    out = recovery.run_once(conn)
    assert [o["result"] for o in out] == ["completed"]
    assert len(list((tmp_path / "success").glob(f"{name[:-4]}*"))) == 1
    with _db()() as s:
        n_docs = s.execute(text("SELECT count(*) FROM source_document WHERE sha256=:h"),
                           {"h": hashlib.sha256(raw).hexdigest()}).scalar_one()
        state = s.execute(text("SELECT state FROM listener_file WHERE name=:n"), {"n": name}).scalar_one()
    assert n_docs == 1 and state == "completed" and len(set(runs)) == 1     # one result, saved once


def test_skipped_files_are_recorded_with_the_reason(conn, tmp_path):
    from cdi_adapter.listener import service

    tag = uuid.uuid4().hex[:8]
    for n in (f"~${tag}.pdf", f"memo-{tag}.docx", f"half-{tag}.part"):
        (tmp_path / "inbox" / n).write_bytes(b"x")
    service.observe(conn)
    service.observe(conn)                                       # seen twice: counted, not duplicated
    with _db()() as s:
        rows = {r[0]: (r[1], r[2]) for r in s.execute(text(
            "SELECT name, reason, times_seen FROM listener_ignored WHERE name LIKE :p"), {"p": f"%{tag}%"})}
    assert rows[f"memo-{tag}.docx"] == ("file type not allowed", 2)
    assert rows[f"~${tag}.pdf"][0] == "temporary or hidden file" and rows[f"half-{tag}.part"][0] == "temporary or hidden file"


def test_health_shows_the_last_good_poll_and_the_stall_rule(conn, tmp_path, monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.listener import service
    from cdi_adapter.listener.health import health

    conn.name = "local"
    with _db()() as s:
        s.execute(text("DELETE FROM listener_heartbeat WHERE connector='local'"))
        before = health(s, "local")
    assert before["stalled"] is True and before["last_good_poll_at"] is None          # never polled: not healthy

    service._beat(conn, ok=True)
    with _db()() as s:
        h = health(s, "local")
    assert h["stalled"] is False and h["seconds_since_last_good_poll"] <= 5
    assert {"files_waiting", "files_in_error", "files_in_quarantine", "oldest_waiting_age_seconds"} <= set(h)

    with _db()() as s:
        s.execute(text("UPDATE listener_heartbeat SET last_poll_ok_at = now() - interval '6 minutes' WHERE connector='local'"))
        assert health(s, "local")["stalled"] is True                                  # > 300 s without a good poll
    service._beat(conn, ok=False, error="network cut")                                # a failed poll does not refresh it
    with _db()() as s:
        h = health(s, "local")
    assert h["stalled"] is True and h["last_error"] == "network cut"
    service._beat(conn, ok=False, error="x", stopped="sign-in missing or expired")
    with _db()() as s:
        assert health(s, "local")["stopped_reason"] == "sign-in missing or expired"
    monkeypatch.setattr(settings, "listener_stall_seconds", 10_000)
    service._beat(conn, ok=True)
    with _db()() as s:
        assert health(s, "local")["stalled"] is False


def test_a_result_is_found_by_the_name_of_the_file_that_was_dropped(fake_store, monkeypatch):
    from cdi_adapter import repo
    from cdi_adapter.listener.health import find_documents

    tag = uuid.uuid4().hex[:8]
    raw = _png()
    with _db()() as s:
        did = str(repo.insert_source_document(
            s, sha256=hashlib.sha256(raw).hexdigest(), mime_type="image/png", object_uri="s3://t/x", byte_size=len(raw),
            source_channel="listener", original_filename=f"scan_{tag}.png"))
        s.execute(text("INSERT INTO listener_file (connector, remote_id, name, etag, state, document_id) "
                       "VALUES ('local', :r, :n, 'e', 'completed', :d)"), {"r": f"/x/{tag}", "n": f"drop {tag}.png", "d": did})
    with _db()() as s:
        by_dropped = find_documents(s, f"drop {tag}")
        by_original = find_documents(s, f"SCAN_{tag}")
        wildcard = find_documents(s, "%")                      # a % in the search is a literal %, not "everything"
    assert by_dropped[0]["document_id"] == did and by_dropped[0]["drive"] == "local"
    assert by_dropped[0]["result"] == f"api/documents/{did}/result.json"
    assert by_original[0]["document_id"] == did
    assert all("%" in (r["filename"] or "") or "%" in (r["dropped_file_name"] or "") for r in wildcard)
