"""The folder object store (SW-S6): the second ``ObjectStore`` adapter, so a deployment needs no S3 server.

A key is a relative path under ``CDI_OBJECT_STORE_DIR`` (put that folder on an encrypted volume). Writes are
atomic (temp file, then rename); a key can never leave the folder (no ``..``, no absolute path, no symlink
escape); the URI is ``file://<key>`` so a document saved here is readable after a switch back.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .config import settings


class FilesystemStore:
    name = "filesystem"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.object_store_dir).resolve()

    def _path(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or "\x00" in key:
            raise ValueError(f"not a valid object key: {key!r}")
        parts = key.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"not a valid object key: {key!r}")
        p = (self.root / Path(*parts)).resolve()
        if self.root != p and self.root not in p.parents:       # a symlink pointing out of the folder
            raise ValueError(f"object key leaves the store: {key!r}")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return self.uri(key)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        try:
            return p.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(key) from exc

    def uri(self, key: str) -> str:
        return f"file://{key}"

    @staticmethod
    def key_from_uri(uri: str) -> str:
        return uri[len("file://"):] if uri.startswith("file://") else uri

    def ping(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".ping"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
