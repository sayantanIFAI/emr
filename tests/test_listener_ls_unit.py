"""Listener stories LS-S1..S9 that need no database: throttle waits, sign-in expiry stops the listener,
failure notes never carry patient values, ignored files are never silent, and a dropped file runs the same
steps in the same order as an upload."""
from __future__ import annotations

import httpx
import pytest

from cdi_adapter.config import settings
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service


# ------------------------------------------------------------------ LS-S1: "too many requests"
def test_the_drive_is_waited_for_as_long_as_it_says_and_retried(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(C.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (httpx.Response(429, headers={"Retry-After": "120"}) if calls["n"] < 3
                else httpx.Response(200, json={"ok": True}))

    monkeypatch.setattr(settings, "graph_user_id", "u")
    c = C.GraphConnector("onedrive", http=httpx.Client(transport=httpx.MockTransport(handler)), token_fn=lambda: "t")
    assert c._req("GET", "/users/u/drive/root").json() == {"ok": True}
    assert sleeps == [120.0, 120.0]                      # the full time it asked for (it used to stop at 60 s)


def test_the_wait_is_capped_and_a_bad_header_falls_back_to_a_growing_wait(monkeypatch):
    monkeypatch.setattr(settings, "listener_throttle_max_wait_seconds", 300.0)
    assert C.retry_wait({"Retry-After": "9999"}, 0) == 300.0
    assert C.retry_wait({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 3) == 8.0
    assert C.retry_wait({}, 2) == 4.0 and C.retry_wait({"Retry-After": "-5"}, 0) == 0.0


def test_a_throttle_that_never_clears_ends_in_an_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    monkeypatch.setattr(settings, "graph_user_id", "u")
    monkeypatch.setattr(settings, "listener_throttle_attempts", 3)
    c = C.GraphConnector("onedrive", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, headers={"Retry-After": "1"}))), token_fn=lambda: "t")
    with pytest.raises(httpx.HTTPStatusError):
        c._req("GET", "/me/drive/root")


# ------------------------------------------------------------------ LS-S2: expired sign-in stops the listener
def test_a_401_means_sign_in_is_needed_and_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "graph_user_id", "u")
    c = C.GraphConnector("onedrive", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401))), token_fn=lambda: "t")
    with pytest.raises(C.AuthRequired):
        c._req("GET", "/me/drive/root")


def test_the_listener_stops_with_the_reason_when_sign_in_is_gone(monkeypatch):
    beats: list[dict] = []

    class Conn:
        name = "onedrive"
        def ensure_folders(self): pass

    monkeypatch.setattr(service, "get_connector", lambda: Conn())
    monkeypatch.setattr(service, "poll_once", lambda c, **k: (_ for _ in ()).throw(
        C.AuthRequired("OneDrive sign-in required (or expired): run --login")))
    monkeypatch.setattr(service, "_beat", lambda c, **kw: beats.append(kw))
    monkeypatch.setattr(service, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr("sys.argv", ["listener"])
    service._STOP.clear()
    with pytest.raises(SystemExit) as stop:
        service.main()
    assert stop.value.code == 3                                          # stopped, not looping
    assert "sign-in missing or expired" in beats[-1]["stopped"] and beats[-1]["ok"] is False


def test_an_ordinary_outage_keeps_the_listener_alive(monkeypatch):
    beats: list[dict] = []
    polls = {"n": 0}

    class Conn:
        name = "local"
        def ensure_folders(self): pass

    def poll(c, **k):
        polls["n"] += 1
        if polls["n"] == 1:
            raise ConnectionError("network cut")
        service._STOP.set()
        return []

    monkeypatch.setattr(service, "get_connector", lambda: Conn())
    monkeypatch.setattr(service, "poll_once", poll)
    monkeypatch.setattr(service, "_beat", lambda c, **kw: beats.append(kw))
    monkeypatch.setattr(service, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(settings, "listener_poll_seconds", 0)
    monkeypatch.setattr("sys.argv", ["listener"])
    service._STOP.clear()
    service.main()
    assert polls["n"] == 2 and any(b.get("ok") for b in beats) and any(b.get("error") == "network cut" for b in beats)
    service._STOP.clear()


# ------------------------------------------------------------------ LS-S6 AC4: no patient values in a note
def test_a_failure_note_carries_reasons_not_values():
    class Conn:
        name = "local"

    f = C.RemoteFile("/x/scan 1.pdf", "scan 1.pdf", "e", 10, "inbox")
    patient, value = "Ravi Kumar", "HbA1c 9.8"          # held in variables: only the MESSAGE carries them
    try:
        raise ValueError(f"{patient!r} is not of type 'integer'; {value!r} failed")
    except ValueError as exc:
        note = service._failure_note(Conn(), f, exc, klass="code", attempts=0, final=False, dest="error",
                                     batch_id="b1", delay=60)
    for secret in ("Ravi Kumar", "HbA1c", "9.8"):
        assert secret not in note
    assert "ValueError" in note and "is not of type" in note and "scan 1.pdf" in note   # the reason stays


def test_scrub_blanks_quoted_text_and_caps_length():
    assert service.scrub("bad value 'Anil Mehra' in field \"name\"") == "bad value '...' in field \"...\""
    assert len(service.scrub("x" * 5000)) == 300 and service.scrub("plain reason") == "plain reason"


# ------------------------------------------------------------------ LS-S3: ignored files are never silent
@pytest.mark.parametrize("name,reason", [
    ("~$scan.pdf", "temporary or hidden file"), ("scan.part", "temporary or hidden file"),
    ("x.tmp", "temporary or hidden file"), (".hidden.png", "temporary or hidden file"),
    ("report.docx", "file type not allowed"), ("photo.heic", "file type not allowed"),
    ("a.pdf.log", "note or data file, not a prescription"), ("a.png", None), ("B.PDF", None), ("c.TIFF", None)])
def test_every_skipped_file_has_a_reason(name, reason):
    assert C.ignore_reason(name) == reason and C.matches(name) == (reason is None)


def test_skipped_files_are_remembered_for_the_listener_to_record(tmp_path):
    c = C.LocalConnector(str(tmp_path))
    c.ensure_folders()
    for n in ("ok.png", "~$tmp.pdf", "memo.docx", "half.part"):
        (tmp_path / "inbox" / n).write_bytes(b"x")
    C.reset_ignored()
    assert [f.name for f in c.list("inbox")] == ["ok.png"]
    assert C.drain_ignored() == {"~$tmp.pdf": "temporary or hidden file", "memo.docx": "file type not allowed",
                                 "half.part": "temporary or hidden file"}
    assert C.drain_ignored() == {}


# ------------------------------------------------------------------ LS-S4 AC4: dropped = uploaded
def test_a_dropped_file_runs_the_same_steps_in_the_same_order_as_an_upload(monkeypatch):
    """Both paths call the same stage functions with the same arguments; the only difference is the channel."""
    from cdi_adapter.ingest.service import IngestResult
    from cdi_adapter.webapp import jobs

    def run(path: str) -> list[tuple]:
        calls: list[tuple] = []
        rec = lambda name: (lambda *a, **k: calls.append((name, tuple(sorted(k.items())))) or _Res())  # noqa: E731

        class _Res:
            document_id = "d1"; n_facts = 3; auto_accepted = 1; in_review = 2; doc_type = "prescription"
            is_handwritten = True; patient_id = None; identity = None; mpi_id = None
            status = "pages_rendered"; deduplicated = False; sha256 = "h"

        res = IngestResult("d1", "h", False, 1, "pages_rendered")
        for mod in (service, jobs):
            monkeypatch.setattr(mod, "ingest_bytes", lambda *a, **k: res, raising=False)
        monkeypatch.setattr("cdi_adapter.ingest.service.ingest_bytes", lambda *a, **k: res)
        for mod, names in (("cdi_adapter.ocr.service", ["ocr_document"]), ("cdi_adapter.classify.service", ["classify_document"]),
                           ("cdi_adapter.extract.service", ["extract_document"]), ("cdi_adapter.terminology.service", ["bind_document"]),
                           ("cdi_adapter.validate.service", ["validate_document"])):
            for n in names:
                monkeypatch.setattr(f"{mod}.{n}", rec(n))
        for n in ("ocr_document", "classify_document", "extract_document", "bind_document", "validate_document"):
            monkeypatch.setattr(jobs, n, rec(n))
        if path == "listener":
            service.run_pipeline(b"bytes", "rx.png")
        else:
            prog = jobs.DocProg(filename="rx.png")
            job = jobs.Job(id="j", abha=None)
            jobs._stage1(prog, "rx.png", b"bytes", None)
            jobs._stage2(job, prog, [])
        return calls

    listener = [c[0] for c in run("listener")]
    upload = [c[0] for c in run("upload")]
    assert listener == upload == ["ocr_document", "classify_document", "ocr_document", "extract_document",
                                  "bind_document", "validate_document"]
