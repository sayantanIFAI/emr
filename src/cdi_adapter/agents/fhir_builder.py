"""FHIR builder agent (E17-S3..S5): fhir_outbox -> ABDM bundle -> blob store + index.

    validate / review decision ──► fhir_outbox (pending, one per document)
    agent: claim (FOR UPDATE SKIP LOCKED) ──► project_document() ──► bundle JSON
        ──► object store  fhir/<patient>/<document>/<artifact>/v<n>.json   (immutable)
        ──► fhir_bundle_blob (sha256, size, status, governed/held counts)

At most ``fhir_agent_max_attempts`` (3, also a DB CHECK) tries per request with
exponential back-off; then the row is ``dead`` and visible to operators. Only governed
facts are asserted - held facts stay out of the bundle (project_document's rule).

Run:  python -m cdi_adapter.agents.fhir_builder         (loop)
      python -m cdi_adapter.agents.fhir_builder --once  (drain once, for cron / tests)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from typing import Any

from sqlalchemy import text

from .. import storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger

log = get_logger(__name__)
WORKER = f"fhir-agent@{socket.gethostname()}"


def _claim(limit: int = 5) -> list[dict[str, Any]]:
    with session_scope() as sess:
        rows = sess.execute(text(
            """
            UPDATE fhir_outbox SET status = 'processing', attempts = attempts + 1,
                   updated_at = now()
             WHERE id IN (SELECT id FROM fhir_outbox
                           WHERE status = 'pending' AND next_attempt_at <= now()
                             AND attempts < :max
                           ORDER BY next_attempt_at
                           LIMIT :n FOR UPDATE SKIP LOCKED)
            RETURNING *
            """), {"n": limit, "max": settings.fhir_agent_max_attempts}).mappings().all()
        return [dict(r) for r in rows]


def build_one(row: dict[str, Any]) -> dict[str, Any]:
    from ..fhir.service import project_document

    res = project_document(str(row["document_id"]))
    body = json.dumps(res["bundle"], sort_keys=True, default=str, ensure_ascii=False).encode()
    sha = hashlib.sha256(body).hexdigest()
    with session_scope() as sess:
        prev = sess.execute(text(
            "SELECT version, sha256 FROM fhir_bundle_blob WHERE document_id = :d "
            "AND artifact_type = :a ORDER BY version DESC LIMIT 1"),
            {"d": str(row["document_id"]), "a": res["artifact_type"]}).mappings().first()
        if prev and prev["sha256"] == sha:
            return {"version": prev["version"], "unchanged": True, **_summary(res)}
        ver = (prev["version"] + 1) if prev else 1
        key = (f"fhir/{row['patient_id']}/{row['document_id']}/"
               f"{res['artifact_type']}/v{ver}.json")
        storage.put_bytes(key, body, "application/fhir+json")
        sess.execute(text(
            """
            INSERT INTO fhir_bundle_blob (patient_id, document_id, artifact_type, version,
               object_key, sha256, byte_size, bundle_status, validation_status, validator,
               governed_facts, held_facts)
            VALUES (:p, :d, :a, :v, :k, :s, :b, :bs, :vs, 'lint-v1', :g, :h)
            """), {"p": str(row["patient_id"]), "d": str(row["document_id"]),
                   "a": res["artifact_type"], "v": ver, "k": key, "s": sha, "b": len(body),
                   "bs": res["bundle_status"],
                   "vs": "error" if any(i.get("severity") == "error" for i in res["issues"])
                   else ("warning" if res["held_facts"] else "valid"),
                   "g": res["asserted_facts"], "h": len(res["held_facts"])})
    return {"version": ver, "object_key": key, **_summary(res)}


def _summary(res: dict[str, Any]) -> dict[str, Any]:
    return {"artifact": res["artifact_type"], "status": res["bundle_status"],
            "asserted": res["asserted_facts"], "held": len(res["held_facts"])}


def _finish(row: dict[str, Any], *, ok: bool, error: str | None = None) -> str:
    with session_scope() as sess:
        if ok:
            sess.execute(text("UPDATE fhir_outbox SET status='done', last_error=NULL, "
                              "updated_at=now() WHERE id=:i"), {"i": row["id"]})
            return "done"
        newer = sess.execute(text(
            "SELECT 1 FROM fhir_outbox WHERE document_id=:d AND status='pending' AND id<>:i"),
            {"d": str(row["document_id"]), "i": row["id"]}).first()
        if newer:        # a newer request exists - it will rebuild; retire this one
            sess.execute(text("UPDATE fhir_outbox SET status='done', last_error=:e, "
                              "updated_at=now() WHERE id=:i"),
                         {"e": f"superseded after error: {error}"[:500], "i": row["id"]})
            return "superseded"
        dead = row["attempts"] >= settings.fhir_agent_max_attempts
        delay = 30 * (2 ** (row["attempts"] - 1))
        sess.execute(text(
            "UPDATE fhir_outbox SET status=:s, last_error=:e, updated_at=now(), "
            "next_attempt_at = now() + make_interval(secs => :d) WHERE id=:i"),
            {"s": "dead" if dead else "pending", "e": (error or "")[:500], "d": delay,
             "i": row["id"]})
        return "dead" if dead else "retry"


def run_once(limit: int = 5) -> list[dict[str, Any]]:
    out = []
    for row in _claim(limit):
        try:
            info = build_one(row)
            _finish(row, ok=True)
            log.info("fhir_built", document_id=str(row["document_id"]), **info)
            out.append({"id": row["id"], "result": "done", **info})
        except Exception as exc:  # noqa: BLE001
            res = _finish(row, ok=False, error=f"{type(exc).__name__}: {exc}")
            log.error("fhir_build_failed", document_id=str(row["document_id"]),
                      attempt=row["attempts"], outcome=res, error=str(exc)[:200])
            out.append({"id": row["id"], "result": res, "error": str(exc)[:200]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="FHIR builder agent (fhir_outbox -> blob)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.once:
        print(json.dumps(run_once(50), default=str, indent=2))
        return
    log.info("fhir_agent_started", worker=WORKER, poll=settings.fhir_agent_poll_seconds)
    while True:
        if not run_once():
            time.sleep(settings.fhir_agent_poll_seconds)


if __name__ == "__main__":
    main()
