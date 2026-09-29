"""Storage connectors for the file listener (E16-S1..S3).

One interface, three back-ends chosen by ``CDI_LISTENER_CONNECTOR``:
  local       a folder on disk / a mounted share
  onedrive    a OneDrive for Business drive (Microsoft Graph, app-only auth)
  sharepoint  a SharePoint document library (Microsoft Graph, app-only auth)

Every connector exposes the same lifecycle folders under ``listener_root``:
  inbox/  processing/  completed/  error/  quarantine/
Moves are server-side renames (Graph PATCH parentReference) - bytes are never re-uploaded.
"""
from __future__ import annotations

import fnmatch
import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

FOLDERS = ("inbox", "processing", "completed", "error", "quarantine")


@dataclass
class RemoteFile:
    remote_id: str          # local: absolute path | graph: driveItem id
    name: str
    etag: str               # version marker: changes whenever the content changes
    size: int
    folder: str             # which lifecycle folder it currently sits in


class Connector(Protocol):
    name: str

    def ensure_folders(self) -> None: ...
    def list(self, folder: str) -> list[RemoteFile]: ...
    def download(self, f: RemoteFile) -> bytes: ...
    def move(self, f: RemoteFile, folder: str) -> RemoteFile: ...
    def write_note(self, folder: str, name: str, content: str) -> None: ...


def folder_name(folder: str) -> str:
    return getattr(settings, f"listener_{folder}")


def matches(name: str) -> bool:
    n = name.casefold()
    if n.endswith((".part", ".tmp", ".crdownload", ".error.txt", ".json")) or n.startswith(("~$", ".")):
        return False
    return any(fnmatch.fnmatch(n, p.casefold()) for p in settings.listener_patterns)


# --------------------------------------------------------------------------- #
class LocalConnector:
    name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.listener_root)

    def _dir(self, folder: str) -> Path:
        return self.root / folder_name(folder)

    def ensure_folders(self) -> None:
        for f in FOLDERS:
            self._dir(f).mkdir(parents=True, exist_ok=True)

    def list(self, folder: str) -> list[RemoteFile]:
        out = []
        for p in sorted(self._dir(folder).iterdir()):
            if p.is_file() and matches(p.name):
                st = p.stat()
                out.append(RemoteFile(str(p.resolve()), p.name,
                                      f"{st.st_size}-{st.st_mtime_ns}", st.st_size, folder))
        return out

    def download(self, f: RemoteFile) -> bytes:
        return Path(f.remote_id).read_bytes()

    def move(self, f: RemoteFile, folder: str) -> RemoteFile:
        src = Path(f.remote_id)
        dest = self._dir(folder) / f.name
        if dest.exists():                      # never overwrite: keep both, suffix a stamp
            dest = dest.with_name(f"{dest.stem}.{int(time.time())}{dest.suffix}")
        shutil.move(str(src), str(dest))
        st = dest.stat()
        return RemoteFile(str(dest.resolve()), dest.name, f"{st.st_size}-{st.st_mtime_ns}",
                          st.st_size, folder)

    def write_note(self, folder: str, name: str, content: str) -> None:
        (self._dir(folder) / name).write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
class GraphConnector:
    """OneDrive / SharePoint through Microsoft Graph with app-only (client-credential)
    auth via MSAL. Certificate credentials are preferred; a client secret also works.
    Required Graph application permission: Files.ReadWrite.All (OneDrive) or
    Sites.Selected / Sites.ReadWrite.All (SharePoint)."""

    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, kind: str) -> None:
        self.name = kind
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.Client(timeout=60.0)
        self._drive_base = self._resolve_drive()
        self._ids: dict[str, str] = {}

    # ---- auth ----
    def _auth(self) -> dict[str, str]:
        if self._token and time.time() < self._token_exp - 120:
            return {"Authorization": f"Bearer {self._token}"}
        import msal

        cred: object
        if settings.graph_cert_path:
            cred = {"private_key": Path(settings.graph_cert_path).read_text(),
                    "thumbprint": settings.graph_cert_thumbprint}
        else:
            cred = settings.graph_client_secret
        app = msal.ConfidentialClientApplication(
            settings.graph_client_id, client_credential=cred,
            authority=f"https://login.microsoftonline.com/{settings.graph_tenant_id}")
        tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in tok:
            raise RuntimeError(f"graph auth failed: {tok.get('error_description') or tok}")
        self._token = tok["access_token"]
        self._token_exp = time.time() + float(tok.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(4):
            r = self._http.request(method, url if url.startswith("http") else self.GRAPH + url,
                                   headers={**self._auth(), **kw.pop("headers", {})}, **kw)
            if r.status_code in (429, 503, 504):           # throttled: honour Retry-After
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def _resolve_drive(self) -> str:
        if settings.graph_drive_id:
            return f"/drives/{settings.graph_drive_id}"
        if self.name == "sharepoint":
            if not settings.graph_site_id:
                raise ValueError("sharepoint connector needs CDI_GRAPH_SITE_ID or CDI_GRAPH_DRIVE_ID")
            return f"/sites/{settings.graph_site_id}/drive"
        if not settings.graph_user_id:
            raise ValueError("onedrive connector needs CDI_GRAPH_DRIVE_ID or CDI_GRAPH_USER_ID")
        return f"/users/{settings.graph_user_id}/drive"

    def _path(self, folder: str) -> str:
        return f"{settings.listener_root.strip('/')}/{folder_name(folder)}"

    def _folder_id(self, folder: str) -> str:
        if folder not in self._ids:
            r = self._req("GET", f"{self._drive_base}/root:/{self._path(folder)}")
            self._ids[folder] = r.json()["id"]
        return self._ids[folder]

    # ---- interface ----
    def ensure_folders(self) -> None:
        for f in FOLDERS:
            try:
                self._folder_id(f)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                parent, _, leaf = self._path(f).rpartition("/")
                url = (f"{self._drive_base}/root:/{parent}:/children" if parent
                       else f"{self._drive_base}/root/children")
                r = self._req("POST", url, json={"name": leaf, "folder": {},
                                                 "@microsoft.graph.conflictBehavior": "replace"})
                self._ids[f] = r.json()["id"]

    def list(self, folder: str) -> list[RemoteFile]:
        out: list[RemoteFile] = []
        url = f"{self._drive_base}/items/{self._folder_id(folder)}/children?$top=200"
        while url:
            js = self._req("GET", url).json()
            for it in js.get("value", []):
                if "file" in it and matches(it["name"]):
                    out.append(RemoteFile(it["id"], it["name"], it.get("eTag") or it.get("cTag", ""),
                                          int(it.get("size") or 0), folder))
            url = js.get("@odata.nextLink")
        return out

    def download(self, f: RemoteFile) -> bytes:
        r = self._req("GET", f"{self._drive_base}/items/{f.remote_id}/content",
                      follow_redirects=True)
        return r.content

    def move(self, f: RemoteFile, folder: str) -> RemoteFile:
        name = f.name
        body = {"parentReference": {"id": self._folder_id(folder)}, "name": name}
        try:
            r = self._req("PATCH", f"{self._drive_base}/items/{f.remote_id}", json=body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:          # name clash: keep both
                raise
            stem, dot, ext = name.rpartition(".")
            body["name"] = f"{stem}.{int(time.time())}{dot}{ext}" if dot else f"{name}.{int(time.time())}"
            r = self._req("PATCH", f"{self._drive_base}/items/{f.remote_id}", json=body)
        it = r.json()
        return RemoteFile(it["id"], it["name"], it.get("eTag", ""), int(it.get("size") or 0), folder)

    def write_note(self, folder: str, name: str, content: str) -> None:
        self._req("PUT", f"{self._drive_base}/root:/{self._path(folder)}/{name}:/content",
                  content=content.encode("utf-8"), headers={"Content-Type": "text/plain"})


def get_connector(kind: str | None = None) -> Connector:
    k = (kind or settings.listener_connector).lower()
    if k == "local":
        return LocalConnector()
    if k in ("onedrive", "sharepoint"):
        return GraphConnector(k)
    raise ValueError(f"unknown listener connector {k!r} (local | onedrive | sharepoint)")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
