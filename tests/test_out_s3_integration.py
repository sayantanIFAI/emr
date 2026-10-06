"""OUT-S3 against a real PostgreSQL (migration 0008): one upload gives one result, even if repeated or
interrupted. Needs only CDI_DATABASE_URL; the object store is faked."""
from __future__ import annotations

import io
import os
import threading
import time
import uuid

import pytest
from PIL import Image
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.environ.get("CDI_DATABASE_URL"), reason="CDI_DATABASE_URL not set")


@pytest.fixture()
def fake_store(monkeypatch):
    from cdi_adapter import storage

    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "put_bytes", lambda k, d, content_type="": blobs.__setitem__(k, d) or f"s3://t/{k}")
    monkeypatch.setattr(storage, "object_uri", lambda k: f"s3://t/{k}")
    monkeypatch.setattr(storage, "get_bytes", lambda k: blobs[k])
    monkeypatch.setattr(storage, "key_from_uri", lambda u: u.split("s3://t/", 1)[-1])
    return blobs


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (900, 700), "white").save(b, "PNG")
    return b.getvalue() + uuid.uuid4().bytes          # trailing bytes: a unique sha per call


def test_the_same_bytes_sent_many_times_at_once_give_one_document(fake_store, monkeypatch):
    from cdi_adapter.ingest.service import ingest_bytes

    monkeypatch.setattr("cdi_adapter.ingest.service._enqueue_next", lambda d: None)
    raw = _png()
    ids: list[str] = []
    errs: list[Exception] = []

    def go() -> None:
        try:
            ids.append(ingest_bytes(raw, filename="rx.png", source_channel="test").document_id)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs, errs
    assert len(set(ids)) == 1 and len(ids) == 8


def test_an_idempotency_key_holds_across_a_restart(fake_store, monkeypatch):
    from cdi_adapter.webapp import jobs

    monkeypatch.setattr(jobs, "_run_job", lambda jid, files: None)           # no processing: only the key
    key = "k-" + uuid.uuid4().hex[:12]
    first = jobs.create_job(None, [("a.png", _png())], idempotency_key=key)
    jobs._idem.clear()                                                        # a restart forgets the process memory
    assert jobs.create_job(None, [("a.png", _png())], idempotency_key=key) == first
    other = jobs.create_job(None, [("a.png", _png())], idempotency_key="k-" + uuid.uuid4().hex[:12])
    assert other != first


def test_a_failed_start_may_be_retried_with_the_same_key(fake_store, monkeypatch):
    from cdi_adapter.webapp import jobs

    key = "k-" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(jobs, "_create_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        jobs.create_job(None, [("a.png", _png())], idempotency_key=key)
    monkeypatch.undo()
    monkeypatch.setattr(jobs, "_run_job", lambda jid, files: None)
    assert jobs.create_job(None, [("a.png", _png())], idempotency_key=key)   # not stuck on the failed attempt


def _doc(status: str, store: dict, *, job: str | None = None, channel: str = "webapp", attempts: int = 0) -> str:
    from cdi_adapter import repo

    raw = _png()
    store["documents/x/original.png"] = raw
    with __import__("cdi_adapter.db", fromlist=["session_scope"]).session_scope() as s:
        did = str(repo.insert_source_document(
            s, sha256=__import__("hashlib").sha256(raw).hexdigest(), mime_type="image/png",
            object_uri="s3://t/documents/x/original.png", byte_size=len(raw), source_channel=channel,
            original_filename="rx.png"))
        s.execute(text("UPDATE source_document SET status=:st, upload_job_id=:j, resume_attempts=:a WHERE id=:d"),
                  {"st": status, "j": job, "a": attempts, "d": did})
    return did


def _row(did: str):
    from cdi_adapter.db import session_scope

    with session_scope() as s:
        return dict(s.execute(text("SELECT status, error_detail, resume_attempts FROM source_document WHERE id=:d"),
                              {"d": did}).mappings().one())


def test_an_interrupted_upload_is_picked_up_again_and_finishes(fake_store, monkeypatch):
    from cdi_adapter.listener import service
    from cdi_adapter.webapp import resume

    unfinished = _doc("ocr_done", fake_store)
    finished = _doc("validated", fake_store)
    listener_owned = _doc("ocr_done", fake_store, channel="listener")
    ran: list[str] = []

    def fake_pipeline(raw, filename, *, source_channel="listener"):
        ran.append(filename)
        from cdi_adapter.db import session_scope
        with session_scope() as s:
            s.execute(text("UPDATE source_document SET status='validated' WHERE sha256=:h"),
                      {"h": __import__("hashlib").sha256(raw).hexdigest()})
        return {"document_id": "x"}

    monkeypatch.setattr(service, "run_pipeline", fake_pipeline)
    out = resume.resume_unfinished()
    mine = {o["document_id"]: o["result"] for o in out}
    assert mine.get(unfinished) == "resumed"
    assert finished not in mine and listener_owned not in mine          # done / not ours: untouched
    assert _row(unfinished)["status"] == "validated" and _row(unfinished)["resume_attempts"] == 1
    assert resume.resume_unfinished() == [] or unfinished not in {o["document_id"] for o in resume.resume_unfinished()}


def test_a_poison_document_is_parked_and_never_blocks_the_others(fake_store, monkeypatch):
    from cdi_adapter.listener import service
    from cdi_adapter.webapp import resume

    poison = _doc("ocr_done", fake_store)
    good = _doc("ocr_done", fake_store)

    def fake_pipeline(raw, filename, *, source_channel="listener"):
        h = __import__("hashlib").sha256(raw).hexdigest()
        from cdi_adapter.db import session_scope
        with session_scope() as s:
            did = str(s.execute(text("SELECT id FROM source_document WHERE sha256=:h"), {"h": h}).scalar_one())
        if did == poison:
            raise RuntimeError("cannot read this file")
        with session_scope() as s:
            s.execute(text("UPDATE source_document SET status='validated' WHERE id=:d"), {"d": did})
        return {"document_id": did}

    monkeypatch.setattr(service, "run_pipeline", fake_pipeline)
    out = {o["document_id"]: o["result"] for o in resume.resume_unfinished()}
    assert out[poison] == "failed" and out[good] == "resumed"           # the failure did not stop the other one
    assert _row(good)["status"] == "validated"
    assert _row(poison)["status"] == "error" and "could not be resumed" in _row(poison)["error_detail"]


def test_a_document_that_keeps_being_interrupted_is_parked_at_the_cap(fake_store, monkeypatch):
    from cdi_adapter.config import settings
    from cdi_adapter.webapp import resume

    d = _doc("ocr_done", fake_store, attempts=settings.resume_max_attempts)
    assert resume.resume_unfinished() == [] or d not in {o["document_id"] for o in resume.resume_unfinished()}
    row = _row(d)
    assert row["status"] == "error" and "picked up again" in row["error_detail"]


def test_a_job_is_shown_again_after_a_restart_from_the_database(fake_store):
    from cdi_adapter.webapp import jobs

    job = "j" + uuid.uuid4().hex[:10]
    a = _doc("validated", fake_store, job=job)
    b = _doc("ocr_done", fake_store, job=job)
    view = jobs.get_job(job).public()
    assert view["state"] == "running"
    assert {d["document_id"]: d["status"] for d in view["documents"]} == {a: "done", b: "running"}
    with __import__("cdi_adapter.db", fromlist=["session_scope"]).session_scope() as s:
        s.execute(text("UPDATE source_document SET status='error', error_detail='rescan' WHERE id=:d"), {"d": b})
    view = jobs.get_job(job).public()
    assert view["state"] in ("done", "review") and {d["status"] for d in view["documents"]} == {"done", "error"}
    assert jobs.get_job("nope" + uuid.uuid4().hex) is None


def test_one_corrupt_document_does_not_stop_the_others_in_a_job(fake_store, monkeypatch):
    from cdi_adapter.webapp import jobs

    def stage1(prog, fn, raw, abha):
        if fn == "bad.pdf":
            prog.status = "error"
            prog.error = "not a PDF"
            raise ValueError("not a PDF")
        prog.document_id = str(uuid.uuid4())
        prog.status = "running"

    monkeypatch.setattr(jobs, "_stage1", stage1)
    monkeypatch.setattr(jobs, "_stage2", lambda job, prog, cands: setattr(prog, "status", "done"))
    jid = jobs.create_job(None, [("a.png", b"1"), ("bad.pdf", b"2"), ("c.png", b"3")])
    for _ in range(100):
        j = jobs.get_job(jid)
        if j.state not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert {d.filename: d.status for d in j.docs} == {"a.png": "done", "bad.pdf": "error", "c.png": "done"}
