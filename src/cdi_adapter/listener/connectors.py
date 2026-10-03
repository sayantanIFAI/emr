"""Storage connectors for the file listener (E16-S1..S3).

One interface, chosen **by configuration only** (``CDI_LISTENER_CONNECTOR``):

  local        a folder on disk / a mounted share
  onedrive     OneDrive for Business   (Microsoft Graph; app-only OR device-code sign-in)
  sharepoint   a SharePoint library    (Microsoft Graph; app-only OR device-code sign-in)
  gdrive       Google Drive / shared drive (Drive v3; service account OR OAuth refresh token)
  pkg.module:Class   any out-of-tree connector class (no-arg constructor, same interface)

Switching OneDrive -> Google Drive is therefore an ``.env`` change, not a code change.

Every connector exposes the same lifecycle folders under ``listener_root``; the physical
folder names are configuration too (``CDI_LISTENER_INBOX`` ... ``CDI_LISTENER_LOG``):

  inbox/       where prescriptions are dropped ("." = the root folder itself)
  processing/  a file being worked on (lease held)
  success/     the pipeline result was durably recorded           (logical name: completed)
  error/       failed; the recovery agent retries it (max 3) unless it is a data error
  quarantine/  rejected up front, or retries exhausted - needs a person
  log/         failure-reason notes ONLY (one small file per failed run)

Moves are server-side renames (Graph PATCH parentReference / Drive addParents) - the bytes
are never re-uploaded. Nothing is ever deleted from the drive.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib
import json
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)

FOLDERS = ("inbox", "processing", "completed", "error", "quarantine", "log")


@dataclass
class RemoteFile:
    remote_id: str          # local: absolute path | graph: driveItem id | gdrive: file id
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


def is_root(folder: str) -> bool:
    """The inbox may be the root folder itself (CDI_LISTENER_INBOX=.)."""
    return folder_name(folder).strip() in ("", ".", "/")


def matches(name: str) -> bool:
    n = name.casefold()
    if n.endswith((".part", ".tmp", ".crdownload", ".error.txt", ".json", ".log")) \
            or n.startswith(("~$", ".")):
        return False
    return any(fnmatch.fnmatch(n, p.casefold()) for p in settings.listener_patterns)


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime())


# --------------------------------------------------------------------------- #
# registry: the connector is picked by name from configuration
# --------------------------------------------------------------------------- #
_REGISTRY: dict[str, Callable[[], Connector]] = {}


def register(*names: str) -> Callable[[Callable[[], Connector]], Callable[[], Connector]]:
    def deco(factory: Callable[[], Connector]) -> Callable[[], Connector]:
        for n in names:
            _REGISTRY[n.lower()] = factory
        return factory
    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_connector(kind: str | None = None) -> Connector:
    k = (kind or settings.listener_connector).strip()
    factory = _REGISTRY.get(k.lower())
    if factory is not None:
        return factory()
    if ":" in k:                                    # out-of-tree plug-in: "package.module:Class"
        mod, _, attr = k.partition(":")
        return getattr(importlib.import_module(mod), attr)()
    raise ValueError(f"unknown listener connector {k!r}; available: {', '.join(available())} "
                     "or 'package.module:Class'")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- #
class LocalConnector:
    name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.listener_root)

    def _dir(self, folder: str) -> Path:
        return self.root if is_root(folder) else self.root / folder_name(folder)

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
        d = self._dir(folder)
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        if p.exists():
            p = p.with_name(f"{p.stem}.{uuid.uuid4().hex[:6]}{p.suffix}")
        p.write_text(content, encoding="utf-8")


register("local")(lambda: LocalConnector())


# --------------------------------------------------------------------------- #
class _RestConnector:
    """Shared HTTP plumbing for the cloud connectors: bearer auth + throttle back-off.

    ``http`` and ``token_fn`` can be injected, which is how the contract tests drive the
    real connector code against an in-memory fake of the remote API."""

    name = "rest"
    _RETRY = (429, 502, 503, 504)

    def __init__(self, http: httpx.Client | None = None,
                 token_fn: Callable[[], str] | None = None) -> None:
        self._http = http or httpx.Client(timeout=60.0)
        self._token_fn = token_fn

    def _bearer(self) -> str:                      # pragma: no cover - overridden
        raise NotImplementedError

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        extra = kw.pop("headers", {})
        r: httpx.Response | None = None
        for attempt in range(4):
            r = self._http.request(method, url, headers={"Authorization": f"Bearer {self._bearer()}",
                                                         **extra}, **kw)
            if r.status_code in self._RETRY:        # throttled / transient: honour Retry-After
                time.sleep(min(60.0, float(r.headers.get("Retry-After", 2 ** attempt))))
                continue
            r.raise_for_status()
            return r
        assert r is not None
        r.raise_for_status()
        return r


# --------------------------------------------------------------------------- #
class GraphConnector(_RestConnector):
    """OneDrive / SharePoint through Microsoft Graph.

    Auth (``CDI_GRAPH_AUTH``):
      app          app-only client-credential flow (certificate preferred, secret works).
                   Needs a Graph *application* permission with admin consent:
                   Files.ReadWrite.All (OneDrive) or Sites.Selected (SharePoint).
      device_code  delegated sign-in as a person, ONCE (``--login``); the refresh token is
                   cached in ``CDI_GRAPH_TOKEN_CACHE`` and renewed silently. Needs only a
                   public-client app registration and the person's own consent - no admin.
    """

    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, kind: str, http: httpx.Client | None = None,
                 token_fn: Callable[[], str] | None = None) -> None:
        super().__init__(http, token_fn)
        self.name = kind
        self._token: str | None = None
        self._token_exp = 0.0
        self._drive_base = self._resolve_drive()
        self._ids: dict[str, str] = {}

    # ---- auth ----
    def _scopes(self) -> list[str]:
        return list(settings.graph_scopes)

    def _msal(self):
        import msal

        authority = f"https://login.microsoftonline.com/{settings.graph_tenant_id or 'organizations'}"
        if settings.graph_auth == "device_code":
            cache = msal.SerializableTokenCache()
            p = Path(settings.graph_token_cache)
            if p.exists():
                cache.deserialize(p.read_text())
            return msal.PublicClientApplication(settings.graph_client_id, authority=authority,
                                                token_cache=cache), cache
        cred: object
        if settings.graph_cert_path:
            cred = {"private_key": Path(settings.graph_cert_path).read_text(),
                    "thumbprint": settings.graph_cert_thumbprint}
        else:
            cred = settings.graph_client_secret
        return msal.ConfidentialClientApplication(settings.graph_client_id, client_credential=cred,
                                                  authority=authority), None

    @staticmethod
    def _save_cache(cache) -> None:
        if cache is not None and cache.has_state_changed:
            p = Path(settings.graph_token_cache)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(cache.serialize())
            p.chmod(0o600)

    def login(self) -> None:
        """One-time interactive sign-in (device code). The person opens the printed URL in
        any browser and signs in; this process never sees the password."""
        if settings.graph_auth != "device_code":
            print("CDI_GRAPH_AUTH is not 'device_code': nothing to sign in to (app-only auth).")
            return
        app, cache = self._msal()
        flow = app.initiate_device_flow(scopes=self._scopes())
        if "user_code" not in flow:
            raise RuntimeError(f"device-code flow failed: {flow.get('error_description') or flow}")
        print(flow["message"], flush=True)
        res = app.acquire_token_by_device_flow(flow)          # blocks until sign-in completes
        if "access_token" not in res:
            raise RuntimeError(f"sign-in failed: {res.get('error_description') or res}")
        self._save_cache(cache)
        who = (res.get("id_token_claims") or {}).get("preferred_username", "?")
        print(f"signed in as {who}; token cache saved to {settings.graph_token_cache}", flush=True)

    def _bearer(self) -> str:
        if self._token_fn:
            return self._token_fn()
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        app, cache = self._msal()
        if settings.graph_auth == "device_code":
            accounts = app.get_accounts()
            tok = app.acquire_token_silent(self._scopes(), account=accounts[0]) if accounts else None
            if not tok or "access_token" not in tok:
                raise RuntimeError("OneDrive sign-in required (or expired): run "
                                   "`python -m cdi_adapter.listener.service --login`")
            self._save_cache(cache)
        else:
            tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            if "access_token" not in tok:
                raise RuntimeError(f"graph auth failed: {tok.get('error_description') or tok}")
        self._token = tok["access_token"]
        self._token_exp = time.time() + float(tok.get("expires_in", 3600))
        return self._token

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        return super()._req(method, url if url.startswith("http") else self.GRAPH + url, **kw)

    # ---- addressing ----
    def _resolve_drive(self) -> str:
        if settings.graph_drive_id:
            return f"/drives/{settings.graph_drive_id}"
        if self.name == "sharepoint":
            if not settings.graph_site_id:
                raise ValueError("sharepoint connector needs CDI_GRAPH_SITE_ID or CDI_GRAPH_DRIVE_ID")
            return f"/sites/{settings.graph_site_id}/drive"
        if settings.graph_user_id:
            return f"/users/{settings.graph_user_id}/drive"
        if settings.graph_auth == "device_code":
            return "/me/drive"                    # the signed-in person's own OneDrive
        raise ValueError("onedrive connector needs CDI_GRAPH_DRIVE_ID or CDI_GRAPH_USER_ID "
                         "(or CDI_GRAPH_AUTH=device_code to use the signed-in user's drive)")

    def _rel(self, folder: str) -> str:
        parts = [settings.listener_root.strip("/")]
        if not is_root(folder):
            parts.append(folder_name(folder).strip("/"))
        return "/".join(p for p in parts if p)

    def _item(self, rel: str) -> str:
        return f"{self._drive_base}/root:/{quote(rel, safe='/')}" if rel else f"{self._drive_base}/root"

    def _folder_id(self, folder: str) -> str:
        if folder not in self._ids:
            self._ids[folder] = self._req("GET", self._item(self._rel(folder))).json()["id"]
        return self._ids[folder]

    def _mkdir_p(self, rel: str) -> str:
        """Create every missing segment of ``rel``; return the id of the last one."""
        cur = ""
        item_id = ""
        for seg in [s for s in rel.split("/") if s]:
            nxt = f"{cur}/{seg}" if cur else seg
            try:
                item_id = self._req("GET", self._item(nxt)).json()["id"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                url = f"{self._item(cur)}:/children" if cur else f"{self._drive_base}/root/children"
                item_id = self._req("POST", url, json={
                    "name": seg, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}).json()["id"]
            cur = nxt
        return item_id

    # ---- interface ----
    def ensure_folders(self) -> None:
        for f in FOLDERS:
            self._ids[f] = self._mkdir_p(self._rel(f)) or self._ids.get(f, "")
            if not self._ids[f]:                    # root of the drive itself
                self._ids[f] = self._req("GET", f"{self._drive_base}/root").json()["id"]

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
        return self._req("GET", f"{self._drive_base}/items/{f.remote_id}/content",
                         follow_redirects=True).content

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
        rel = f"{self._rel(folder)}/{name}".lstrip("/")
        self._req("PUT", f"{self._drive_base}/root:/{quote(rel, safe='/')}:/content",
                  content=content.encode("utf-8"), headers={"Content-Type": "text/plain"})


register("onedrive")(lambda: GraphConnector("onedrive"))
register("sharepoint")(lambda: GraphConnector("sharepoint"))


# --------------------------------------------------------------------------- #
class GoogleDriveConnector(_RestConnector):
    """Google Drive (My Drive or a shared drive) through the Drive v3 REST API.

    Auth (``CDI_GDRIVE_AUTH``):
      service_account  a key file (``CDI_GDRIVE_CREDENTIALS_FILE``); share the root folder
                       with the service account's e-mail (or use domain-wide delegation
                       with ``CDI_GDRIVE_IMPERSONATE_USER``).
      oauth            a person's refresh token (``CDI_GDRIVE_CLIENT_ID`` / ``_CLIENT_SECRET``
                       / ``_REFRESH_TOKEN``).
    The listener root is ``CDI_GDRIVE_ROOT_FOLDER_ID`` when set, else ``CDI_LISTENER_ROOT``
    resolved as a folder path from the drive root. Install: ``pip install '.[gdrive]'``.
    """

    API = "https://www.googleapis.com/drive/v3"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3"
    FOLDER = "application/vnd.google-apps.folder"
    FIELDS = "id,name,md5Checksum,modifiedTime,size,version"

    def __init__(self, http: httpx.Client | None = None,
                 token_fn: Callable[[], str] | None = None) -> None:
        super().__init__(http, token_fn)
        self.name = "gdrive"
        self._creds = None
        self._greq = None
        self._ids: dict[str, str] = {}
        self._root_id: str | None = None

    # ---- auth ----
    def _bearer(self) -> str:
        if self._token_fn:
            return self._token_fn()
        if self._creds is None:
            from google.auth.transport.requests import Request

            scopes = ["https://www.googleapis.com/auth/drive"]
            if settings.gdrive_auth == "oauth":
                from google.oauth2.credentials import Credentials

                self._creds = Credentials(None, refresh_token=settings.gdrive_refresh_token,
                                          client_id=settings.gdrive_client_id,
                                          client_secret=settings.gdrive_client_secret,
                                          token_uri="https://oauth2.googleapis.com/token",
                                          scopes=scopes)
            else:
                from google.oauth2 import service_account

                self._creds = service_account.Credentials.from_service_account_file(
                    settings.gdrive_credentials_file, scopes=scopes,
                    subject=settings.gdrive_impersonate_user or None)
            self._greq = Request()
        if not self._creds.valid:
            self._creds.refresh(self._greq)
        return self._creds.token

    def login(self) -> None:
        print("Google Drive uses a service-account key or an OAuth refresh token from the "
              ".env file: no interactive sign-in needed.")

    # ---- addressing ----
    @staticmethod
    def _q(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'")

    def _common(self) -> dict[str, str]:
        p = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if settings.gdrive_drive_id:
            p.update({"corpora": "drive", "driveId": settings.gdrive_drive_id})
        return p

    def _child_folder(self, parent: str, name: str) -> str:
        q = (f"mimeType='{self.FOLDER}' and trashed=false and '{parent}' in parents "
             f"and name='{self._q(name)}'")
        files = self._req("GET", f"{self.API}/files", params={
            **self._common(), "q": q, "fields": "files(id,name)", "pageSize": "10"}
        ).json().get("files", [])
        if files:
            return files[0]["id"]
        return self._req("POST", f"{self.API}/files", params={"supportsAllDrives": "true", "fields": "id"},
                         json={"name": name, "mimeType": self.FOLDER, "parents": [parent]}).json()["id"]

    def _root(self) -> str:
        if self._root_id is None:
            if settings.gdrive_root_folder_id:
                self._root_id = settings.gdrive_root_folder_id
            else:
                cur = settings.gdrive_drive_id or "root"
                for seg in [s for s in settings.listener_root.split("/") if s and s != "."]:
                    cur = self._child_folder(cur, seg)
                self._root_id = cur
        return self._root_id

    def _folder_id(self, folder: str) -> str:
        if folder not in self._ids:
            self._ids[folder] = (self._root() if is_root(folder)
                                 else self._child_folder(self._root(), folder_name(folder)))
        return self._ids[folder]

    @staticmethod
    def _remote(it: dict, folder: str) -> RemoteFile:
        etag = it.get("md5Checksum") or f"{it.get('version', '')}-{it.get('modifiedTime', '')}"
        return RemoteFile(it["id"], it["name"], etag, int(it.get("size") or 0), folder)

    # ---- interface ----
    def ensure_folders(self) -> None:
        for f in FOLDERS:
            self._folder_id(f)

    def list(self, folder: str) -> list[RemoteFile]:
        out: list[RemoteFile] = []
        params = {**self._common(), "pageSize": "200",
                  "q": f"'{self._folder_id(folder)}' in parents and trashed=false "
                       f"and mimeType!='{self.FOLDER}'",
                  "fields": f"nextPageToken,files({self.FIELDS})"}
        while True:
            js = self._req("GET", f"{self.API}/files", params=params).json()
            out += [self._remote(it, folder) for it in js.get("files", []) if matches(it["name"])]
            if not js.get("nextPageToken"):
                return out
            params = {**params, "pageToken": js["nextPageToken"]}

    def download(self, f: RemoteFile) -> bytes:
        return self._req("GET", f"{self.API}/files/{f.remote_id}",
                         params={"alt": "media", "supportsAllDrives": "true"},
                         follow_redirects=True).content

    def move(self, f: RemoteFile, folder: str) -> RemoteFile:
        it = self._req("PATCH", f"{self.API}/files/{f.remote_id}", params={
            "addParents": self._folder_id(folder), "removeParents": self._folder_id(f.folder),
            "supportsAllDrives": "true", "fields": self.FIELDS}, json={}).json()
        return self._remote(it, folder)

    def write_note(self, folder: str, name: str, content: str) -> None:
        b = "cdi" + uuid.uuid4().hex
        meta = json.dumps({"name": name, "parents": [self._folder_id(folder)]})
        body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                f"--{b}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{content}\r\n--{b}--"
                ).encode()
        self._req("POST", f"{self.UPLOAD}/files", params={"uploadType": "multipart",
                                                          "supportsAllDrives": "true"},
                  content=body, headers={"Content-Type": f"multipart/related; boundary={b}"})


register("gdrive", "googledrive", "google_drive")(lambda: GoogleDriveConnector())
