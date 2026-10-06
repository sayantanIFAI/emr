"""Listener connectors: one contract, three back-ends, selected by configuration only.

The OneDrive (Graph) and Google Drive connectors are the REAL connector classes driven against
in-memory fakes of the remote APIs (injected httpx transport + token), so these tests prove the
request flow, lifecycle folders, name-clash handling, log notes and the "root folder is the inbox"
mode without any network or credentials. No database needed."""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cdi_adapter.config import settings
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener.connectors import (
    GoogleDriveConnector,
    GraphConnector,
    LocalConnector,
    get_connector,
)


# --------------------------------------------------------------------------- #
# in-memory remote drives
# --------------------------------------------------------------------------- #
class _Tree:
    """id -> {name, parent, folder, data, ver}; shared by both fakes."""

    def __init__(self, root_id: str) -> None:
        self.root = root_id
        self.items: dict[str, dict] = {root_id: {"name": "", "parent": None, "folder": True,
                                                 "data": b"", "ver": 1}}
        self.n = 0

    def add(self, name: str, parent: str, folder: bool = False, data: bytes = b"") -> str:
        self.n += 1
        iid = f"id{self.n}"
        self.items[iid] = {"name": name, "parent": parent, "folder": folder, "data": data, "ver": 1}
        return iid

    def child(self, parent: str, name: str) -> str | None:
        return next((i for i, v in self.items.items() if v["parent"] == parent and v["name"] == name), None)

    def by_path(self, path: str) -> str | None:
        cur: str | None = self.root
        for seg in [s for s in path.split("/") if s and s != "."]:
            cur = self.child(cur, seg) if cur else None
        return cur

    def mkdir_p(self, path: str) -> str:
        cur = self.root
        for seg in [s for s in path.split("/") if s]:
            cur = self.child(cur, seg) or self.add(seg, cur, folder=True)
        return cur

    def names(self, path: str) -> list[str]:
        fid = self.by_path(path)
        return sorted(v["name"] for v in self.items.values()
                      if fid and v["parent"] == fid and not v["folder"])

    def put(self, path: str, name: str, data: bytes) -> str:
        return self.add(name, self.mkdir_p(path), data=data)


class FakeGraph(_Tree):
    def __init__(self) -> None:
        super().__init__("ROOT")

    def _js(self, iid: str) -> dict:
        v = self.items[iid]
        return {"id": iid, "name": v["name"], "size": len(v["data"]), "eTag": f'"{iid}-{v["ver"]}"',
                ("folder" if v["folder"] else "file"): {}}

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = urllib.parse.unquote(req.url.path)
        m = re.match(r"^/v1\.0/(?:users/[^/]+/drive|drives/[^/]+|sites/[^/]+/drive|me/drive)(?P<rest>.*)$", path)
        assert m, path
        rest, meth = m.group("rest"), req.method
        if rest == "/root" and meth == "GET":
            return httpx.Response(200, json=self._js(self.root))
        if rest == "/root/children" and meth == "POST":
            body = json.loads(req.content)
            return httpx.Response(201, json=self._js(self.add(body["name"], self.root, folder=True)))
        mm = re.match(r"^/root:/(?P<p>.*?)(?::/(?P<tail>children|content))?$", rest)
        if mm:
            p, tail = mm.group("p"), mm.group("tail")
            if tail is None and meth == "GET":
                iid = self.by_path(p)
                return httpx.Response(200, json=self._js(iid)) if iid else httpx.Response(404, json={})
            if tail == "children" and meth == "POST":
                parent = self.by_path(p)
                body = json.loads(req.content)
                return httpx.Response(201, json=self._js(self.add(body["name"], parent, folder=True)))
            if tail == "content" and meth == "PUT":
                parent_path, _, name = p.rpartition("/")
                return httpx.Response(201, json=self._js(self.add(name, self.mkdir_p(parent_path), data=req.content)))
        mm = re.match(r"^/items/(?P<id>[^/]+)(?P<tail>/children|/content)?$", rest)
        if mm:
            iid, tail = mm.group("id"), mm.group("tail")
            if tail == "/children":
                return httpx.Response(200, json={"value": [self._js(i) for i, v in self.items.items()
                                                           if v["parent"] == iid]})
            if tail == "/content":
                return httpx.Response(200, content=self.items[iid]["data"])
            if meth == "PATCH":
                body = json.loads(req.content)
                dest, name = body["parentReference"]["id"], body.get("name") or self.items[iid]["name"]
                clash = self.child(dest, name)
                if clash and clash != iid:
                    return httpx.Response(409, json={"error": "nameAlreadyExists"})
                self.items[iid].update(parent=dest, name=name)
                return httpx.Response(200, json=self._js(iid))
        raise AssertionError(f"unhandled {meth} {path}")


class FakeDrive(_Tree):
    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self) -> None:
        super().__init__("root")

    def _js(self, iid: str) -> dict:
        v = self.items[iid]
        return {"id": iid, "name": v["name"], "size": str(len(v["data"])), "version": str(v["ver"]),
                "md5Checksum": f"md5-{iid}-{v['ver']}", "modifiedTime": "2026-10-03T00:00:00Z"}

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, meth, q = req.url.path, req.method, dict(req.url.params)
        if path == "/drive/v3/files" and meth == "GET":
            expr = q["q"]
            m = re.match(r"mimeType='[^']+' and trashed=false and '(?P<p>[^']+)' in parents and name='(?P<n>.*)'$", expr)
            if m:
                hit = self.child(m.group("p"), m.group("n").replace("\\'", "'"))
                return httpx.Response(200, json={"files": [self._js(hit)] if hit else []})
            m = re.match(r"'(?P<p>[^']+)' in parents and trashed=false and mimeType!='[^']+'$", expr)
            assert m, expr
            return httpx.Response(200, json={"files": [self._js(i) for i, v in self.items.items()
                                                       if v["parent"] == m.group("p") and not v["folder"]]})
        if path == "/drive/v3/files" and meth == "POST":
            b = json.loads(req.content)
            return httpx.Response(200, json={"id": self.add(b["name"], b["parents"][0], folder=True)})
        if path == "/upload/drive/v3/files" and meth == "POST":
            boundary = re.search(r"boundary=(\S+)", req.headers["content-type"]).group(1)
            parts = req.content.decode().split(f"--{boundary}")
            meta = json.loads(parts[1].split("\r\n\r\n", 1)[1].strip())
            body = parts[2].split("\r\n\r\n", 1)[1].rsplit("\r\n", 1)[0]
            return httpx.Response(200, json={"id": self.add(meta["name"], meta["parents"][0], data=body.encode())})
        m = re.match(r"^/drive/v3/files/(?P<id>[^/]+)$", path)
        if m and meth == "GET" and q.get("alt") == "media":
            return httpx.Response(200, content=self.items[m.group("id")]["data"])
        if m and meth == "PATCH":
            iid = m.group("id")
            assert self.items[iid]["parent"] == q["removeParents"], "removeParents must be the current parent"
            self.items[iid]["parent"] = q["addParents"]
            return httpx.Response(200, json=self._js(iid))
        raise AssertionError(f"unhandled {meth} {path}")


# --------------------------------------------------------------------------- #
# one contract, three connectors
# --------------------------------------------------------------------------- #
class Harness:
    def __init__(self, conn, names, drop):
        self.conn, self.names, self.drop = conn, names, drop


def _local(tmp_path, monkeypatch) -> Harness:
    conn = LocalConnector(str(tmp_path))

    def drop(folder: str, name: str, data: bytes) -> None:
        d = tmp_path if C.is_root(folder) else tmp_path / C.folder_name(folder)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)

    def names(physical: str) -> list[str]:
        d = tmp_path if physical == "." else tmp_path / physical
        return sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else []
    return Harness(conn, names, drop)


def _graph(tmp_path, monkeypatch) -> Harness:
    monkeypatch.setattr(settings, "listener_root", "OCR")
    monkeypatch.setattr(settings, "graph_user_id", "u@example.com")
    fake = FakeGraph()
    conn = GraphConnector("onedrive", http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                          token_fn=lambda: "tok")

    def drop(folder: str, name: str, data: bytes) -> None:
        fake.put("OCR" if C.is_root(folder) else f"OCR/{C.folder_name(folder)}", name, data)
    return Harness(conn, lambda physical: fake.names("OCR" if physical == "." else f"OCR/{physical}"), drop)


def _gdrive(tmp_path, monkeypatch) -> Harness:
    monkeypatch.setattr(settings, "listener_root", "OCR")
    monkeypatch.setattr(settings, "gdrive_root_folder_id", "")
    fake = FakeDrive()
    conn = GoogleDriveConnector(http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                                token_fn=lambda: "tok")

    def drop(folder: str, name: str, data: bytes) -> None:
        fake.put("OCR" if C.is_root(folder) else f"OCR/{C.folder_name(folder)}", name, data)
    return Harness(conn, lambda physical: fake.names("OCR" if physical == "." else f"OCR/{physical}"), drop)


BACKENDS = {"local": _local, "onedrive": _graph, "gdrive": _gdrive}


@pytest.fixture(params=list(BACKENDS))
def h(request, tmp_path, monkeypatch) -> Harness:
    monkeypatch.setattr(settings, "listener_inbox", "inbox")
    monkeypatch.setattr(settings, "listener_completed", "success")
    return BACKENDS[request.param](tmp_path, monkeypatch)


def test_lifecycle_contract(h: Harness):
    h.conn.ensure_folders()
    h.drop("inbox", "rx1.pdf", b"AAA")
    h.drop("inbox", "~$rx1.pdf", b"lock")        # office lock file: ignored
    h.drop("inbox", "notes.docx", b"x")          # not a prescription format: ignored
    h.drop("inbox", "half.pdf.part", b"x")       # upload in progress: ignored
    files = h.conn.list("inbox")
    assert [f.name for f in files] == ["rx1.pdf"] and files[0].size == 3
    assert h.conn.download(files[0]) == b"AAA"

    f = h.conn.move(files[0], "processing")
    assert h.conn.list("inbox") == [] and [x.name for x in h.conn.list("processing")] == ["rx1.pdf"]
    f = h.conn.move(f, "completed")
    assert h.names("success") == ["rx1.pdf"]                       # physical folder is "success"
    assert h.names("inbox") == ["half.pdf.part", "notes.docx", "~$rx1.pdf"]


def test_name_clash_keeps_both(h: Harness):
    h.conn.ensure_folders()
    h.drop("inbox", "same.pdf", b"one")
    f1 = h.conn.move(h.conn.list("inbox")[0], "completed")
    assert f1.name == "same.pdf"
    h.drop("inbox", "same.pdf", b"two")
    h.conn.move(h.conn.list("inbox")[0], "completed")
    assert len(h.names("success")) == 2                             # nothing overwritten


def test_failure_note_goes_to_log_folder_only(h: Harness):
    h.conn.ensure_folders()
    h.conn.write_note("log", "rx1.pdf.20261003T000000.run1.log", "reason: boom")
    assert h.names("log") == ["rx1.pdf.20261003T000000.run1.log"]
    for other in ("inbox", "processing", "success", "error", "quarantine"):
        assert h.names(other) == []


def test_root_folder_can_be_the_inbox(request, tmp_path, monkeypatch):
    """CDI_LISTENER_INBOX=. -> prescriptions are dropped straight into the OCR folder."""
    for backend in BACKENDS.values():
        h = backend(tmp_path / backend.__name__, monkeypatch)
        monkeypatch.setattr(settings, "listener_inbox", ".")
        h.conn.ensure_folders()
        h.drop("inbox", "rx9.png", b"PNG")
        assert [f.name for f in h.conn.list("inbox")] == ["rx9.png"]
        h.conn.move(h.conn.list("inbox")[0], "processing")
        assert h.names(".") == [] and h.names("processing") == ["rx9.png"]


# --------------------------------------------------------------------------- #
# selection by configuration only
# --------------------------------------------------------------------------- #
class PluginConnector(LocalConnector):
    name = "plugin"


def test_connector_is_selected_by_config_only(monkeypatch):
    monkeypatch.setattr(settings, "graph_user_id", "u@example.com")
    for kind, cls in [("local", LocalConnector), ("onedrive", GraphConnector),
                      ("sharepoint", None), ("gdrive", GoogleDriveConnector),
                      ("GoogleDrive", GoogleDriveConnector)]:
        monkeypatch.setattr(settings, "listener_connector", kind)
        if kind == "sharepoint":
            monkeypatch.setattr(settings, "graph_site_id", "contoso.sharepoint.com,1,2")
            cls = GraphConnector
        assert isinstance(get_connector(), cls), kind
    monkeypatch.setattr(settings, "listener_connector", f"{__name__}:PluginConnector")
    assert isinstance(get_connector(), PluginConnector)           # out-of-tree plug-in by dotted path


def test_unknown_connector_lists_the_choices(monkeypatch):
    monkeypatch.setattr(settings, "listener_connector", "dropbox")
    with pytest.raises(ValueError) as e:
        get_connector()
    assert "gdrive" in str(e.value) and "onedrive" in str(e.value)


def test_onedrive_misconfiguration_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, "graph_drive_id", "")
    monkeypatch.setattr(settings, "graph_user_id", "")
    monkeypatch.setattr(settings, "graph_auth", "app")
    with pytest.raises(ValueError, match="CDI_GRAPH_USER_ID"):
        GraphConnector("onedrive")
    monkeypatch.setattr(settings, "graph_auth", "device_code")
    assert GraphConnector("onedrive")._drive_base == "/me/drive"   # signed-in user's own OneDrive


def test_throttling_is_retried_with_retry_after(monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (httpx.Response(429, headers={"Retry-After": "0"}) if calls["n"] < 3
                else httpx.Response(200, json={"id": "x"}))
    monkeypatch.setattr(settings, "graph_user_id", "u")
    c = GraphConnector("onedrive", http=httpx.Client(transport=httpx.MockTransport(handler)),
                       token_fn=lambda: "t")
    assert c._req("GET", "/users/u/drive/root").json() == {"id": "x"} and calls["n"] == 3


# --------------------------------------------------------------------------- #
# batching (pure)
# --------------------------------------------------------------------------- #
def _rows(n: int, age_s: float) -> list[dict]:
    now = datetime.now(UTC)
    return [{"id": i, "name": f"f{i}", "first_seen_at": now - timedelta(seconds=age_s - i * 0.001)}
            for i in range(n)]


def test_batches_are_three_oldest_first(monkeypatch):
    from cdi_adapter.listener.service import select_batch

    monkeypatch.setattr(settings, "listener_batch_size", 3)
    monkeypatch.setattr(settings, "listener_batch_wait_seconds", 60)
    got = select_batch(_rows(7, age_s=5))
    assert [r["id"] for r in got] == [0, 1, 2]                      # full batch: no waiting


def test_partial_batch_waits_then_flushes(monkeypatch):
    from cdi_adapter.listener.service import select_batch

    monkeypatch.setattr(settings, "listener_batch_size", 3)
    monkeypatch.setattr(settings, "listener_batch_wait_seconds", 60)
    assert select_batch(_rows(2, age_s=10)) == []                   # keep waiting for a 3rd file
    assert len(select_batch(_rows(2, age_s=61))) == 2               # waited long enough: flush
    assert len(select_batch(_rows(1, age_s=1), flush=True)) == 1    # --once: drain
    assert select_batch([]) == []


def test_failure_note_is_actionable():
    from cdi_adapter.listener.connectors import RemoteFile
    from cdi_adapter.listener.service import DataError, _failure_note

    f = RemoteFile("id", "rx1.pdf", "e", 1, "error")
    try:
        raise ConnectionError("model gateway timeout")
    except ConnectionError as exc:
        note = _failure_note(LocalConnector("."), f, exc, klass="transient", attempts=0, final=False,
                             dest="error", batch_id="ab12cd34", delay=60)
    assert "rx1.pdf" in note and "ab12cd34" in note and "model gateway timeout" in note
    assert "run:           1 of 4" in note and "automatic retry 1 of 3 in about 60s" in note
    try:
        raise DataError("rescan: page too blurred")
    except DataError as exc:
        note = _failure_note(LocalConnector("."), f, exc, klass="data", attempts=0, final=True,
                             dest="error", batch_id=None, delay=60)
    assert "NO automatic retry" in note


def test_corrupt_files_are_data_errors_and_outages_are_transient():
    from cdi_adapter.listener.service import DataError, _classify_error

    class FileDataError(RuntimeError):          # what a corrupt-PDF library raises
        pass

    class UnidentifiedImageError(OSError):      # what Pillow raises for a corrupt image
        pass

    assert _classify_error(DataError("rescan")) == "data"
    assert _classify_error(FileDataError("Failed to open stream")) == "data"
    assert _classify_error(RuntimeError("Failed to open stream")) == "data"          # by message
    assert _classify_error(UnidentifiedImageError("cannot identify image file")) == "data"
    assert _classify_error(ConnectionError("broken pipe")) == "transient"            # not "broken" -> data
    assert _classify_error(TimeoutError("model gateway timeout")) == "transient"
    assert _classify_error(RuntimeError("HTTP 503 from gateway")) == "transient"
    assert _classify_error(RuntimeError("CUDA out of memory")) == "code"
