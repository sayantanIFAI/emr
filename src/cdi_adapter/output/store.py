"""Write the result JSON to a file and / or the object store (OUT-S2).

* **All at once**: a temp file in the same folder, then an atomic rename, so a reader never sees half a
  file. The object store write is a single PUT.
* **Same bytes**: the content is :func:`to_bytes` of the result built from the database, which has no
  timestamps and a fixed key order, so writing again gives the identical file.
* **Never an invalid file**: the result is validated against ``schemas/result.v1.json`` first
  (:meth:`JsonPlaceholderConnector.render` does it); a document that fails is not written.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..config import settings
from ..logging import get_logger
from .json_connector import get_connector, to_bytes

log = get_logger(__name__)


def result_key(document_id: str) -> str:
    return f"results/{document_id[:2]}/{document_id}/result.v1.json"


def write_atomic(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)               # atomic on one filesystem: the old file or the new one, never half
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_result(document_id: str) -> dict[str, str]:
    """Build, validate and write the result for ``document_id``. Returns where it went (empty if both
    outputs are off). Raises :class:`ResultInvalid` / ``ValueError`` for a document that cannot be written."""
    where: dict[str, str] = {}
    if not settings.output_dir and not settings.output_object_store:
        return where
    result = get_connector().render(document_id)
    if result is None:
        raise ValueError(f"document {document_id} not found")
    data = to_bytes(result)
    if settings.output_dir:
        where["file"] = str(write_atomic(Path(settings.output_dir) / f"{document_id}.result.v1.json", data))
    if settings.output_object_store:
        from .. import storage

        where["object"] = storage.put_bytes(result_key(document_id), data, "application/json")
    return where


def write_result_quietly(document_id: str) -> None:
    """The pipeline's hook: a failure to write the derived file is logged, never fails the document (the
    endpoint builds the same JSON from the database on demand)."""
    try:
        where = write_result(document_id)
        if where:
            log.info("result_written", document_id=document_id, **where)
    except Exception as exc:  # noqa: BLE001
        log.error("result_write_failed", document_id=document_id, error=str(exc)[:300])
