"""Dispatch agent (E20): governed data -> downstream screens (HIS / EMR front ends).

    rx_* (governed rows only) ──► record view ──► dispatch_mapping (per target+screen,
    versioned) ──► payload shaped to the screen's fields ──► adapter (REST | ui) ──► ack
    ──► dispatch_log (idempotent on target+screen+document+content hash)

Safety: a target is ``enabled`` only with ``approved_by`` + ``approved_at`` (DB CHECK) -
nothing is sent to a new system until a person approves it. Documents with any fact still
in review are not dispatched. At most 3 attempts per payload, then ``dead``.

Mapping document (dispatch_mapping.mapping)::

    {"endpoint": "/api/opd/prescription", "method": "POST",
     "fields": {"patientName": "patient.name", "abha": "patient.abha_number",
                "doctor": "prescription.practitioner_name", "visitDate": "prescription.encounter_date",
                "diagnosis": "diagnoses[*].text|join:, "},
     "lists": {"medicines": {"from": "medications",
                             "fields": {"name": "drug_text", "strength": "strength",
                                        "frequency": "frequency_code", "days": "duration_days"}},
               "tests": {"from": "investigations", "fields": {"name": "order_text", "loinc": "code"}}}}

CLI:
    python -m cdi_adapter.agents.dispatch add-target his1 "City HIS" rest https://his.local HIS1_TOKEN
    python -m cdi_adapter.agents.dispatch load-mapping his1 opd_rx mapping.json
    python -m cdi_adapter.agents.dispatch approve his1 --by "Dr Admin"
    python -m cdi_adapter.agents.dispatch preview his1 opd_rx <document_id>
    python -m cdi_adapter.agents.dispatch run [--once]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Any

import httpx
from sqlalchemy import text

from ..config import settings
from ..db import session_scope
from ..logging import get_logger

log = get_logger(__name__)
_GOVERNED = ("auto_accepted", "clinician_confirmed", "corrected")


# --------------------------------------------------------------------------- #
# record view: everything a screen may need, governed rows only
# --------------------------------------------------------------------------- #
def record_view(sess: Any, document_id: str) -> dict[str, Any] | None:
    rx = sess.execute(text(
        """
        SELECT rp.*, p.name_full AS patient_name, p.gender, p.birth_date, p.abha_number,
               p.mpi_id, cp.name_full AS practitioner_name, cp.registration_number,
               cp.department AS practitioner_department
          FROM rx_prescription rp
          LEFT JOIN patient_identity p ON p.id = rp.patient_id
          LEFT JOIN cn_practitioner cp ON cp.id = rp.practitioner_id
         WHERE rp.document_id = :d
        """), {"d": document_id}).mappings().first()
    if not rx:
        return None

    def rows(table: str, cols: str) -> list[dict[str, Any]]:
        return [dict(r) for r in sess.execute(text(
            f"SELECT {cols} FROM {table} t JOIN clinical_fact f ON f.id = t.fact_id "
            f"WHERE t.prescription_id = :r AND f.is_current AND f.review_state = ANY(:g)"),
            {"r": rx["id"], "g": list(_GOVERNED)}).mappings()]

    meds = rows("rx_medication_order",
                "t.line_no, t.drug_text, t.concept_id, t.code_system, t.code, "
                "t.strength_num AS strength, t.strength_unit, t.form, t.dose_num, t.dose_unit, "
                "t.route, t.frequency_code, t.frequency_per_day, t.duration_days, t.prn, "
                "t.instructions")
    return {
        "document_id": document_id,
        "patient": {"name": rx["patient_name"], "gender": rx["gender"],
                    "birth_date": str(rx["birth_date"]) if rx["birth_date"] else None,
                    "abha_number": rx["abha_number"], "mpi_id": rx["mpi_id"]},
        "prescription": {"doc_type": rx["doc_type"],
                         "encounter_date": rx["encounter_date"].isoformat() if rx["encounter_date"] else None,
                         "practitioner_name": rx["practitioner_name"],
                         "practitioner_reg_no": rx["registration_number"],
                         "practitioner_department": rx["practitioner_department"]},
        "medications": sorted(meds, key=lambda m: m.get("line_no") or 0),
        "investigations": rows("rx_investigation_order", "t.order_text, t.concept_id, "
                               "t.code_system, t.code, t.kind"),
        "diagnoses": rows("rx_diagnosis", "t.diagnosis_text AS text, t.code_system, t.code"),
        "complaints": rows("rx_complaint", "t.complaint_text AS text, t.code_system, t.code"),
        "vitals": rows("rx_vital", "t.name, t.value_num, t.value_text, t.unit, t.code"),
        "advice": rows("rx_advice", "t.advice_text AS text"),
    }


def _pending_review(sess: Any, document_id: str) -> int:
    return sess.execute(text(
        "SELECT count(*) FROM clinical_fact WHERE :d = ANY(source_doc_ids) AND is_current "
        "AND review_state IN ('pending','in_review')"), {"d": document_id}).scalar_one()


# --------------------------------------------------------------------------- #
# mapping
# --------------------------------------------------------------------------- #
def _get(obj: Any, path: str) -> Any:
    pipe = None
    if "|" in path:
        path, pipe = path.split("|", 1)
    cur: Any = obj
    for part in path.split("."):
        if part.endswith("[*]"):
            key = part[:-3]
            cur = [x for x in (cur.get(key) if isinstance(cur, dict) else []) or []]
            continue
        if isinstance(cur, list):
            cur = [x.get(part) if isinstance(x, dict) else None for x in cur]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    if pipe and pipe.startswith("join:") and isinstance(cur, list):
        cur = pipe[5:].join(str(x) for x in cur if x not in (None, ""))
    if hasattr(cur, "__float__") and not isinstance(cur, (int, float, bool)):
        cur = float(cur)
    return cur


def apply_mapping(mapping: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    out = {k: _get(rec, v) for k, v in (mapping.get("fields") or {}).items()}
    for name, spec in (mapping.get("lists") or {}).items():
        out[name] = [{k: _get(item, v) for k, v in spec["fields"].items()}
                     for item in rec.get(spec["from"]) or []]
    return out


# --------------------------------------------------------------------------- #
# adapters
# --------------------------------------------------------------------------- #
def _send(target: dict[str, Any], mapping: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if target["channel"] == "ui":          # the screen pulls it from dispatch_log
        return {"stored": True}
    headers = {"Content-Type": "application/json"}
    if target.get("auth_ref"):
        tok = os.environ.get(target["auth_ref"], "")
        if not tok:
            raise RuntimeError(f"secret {target['auth_ref']!r} is not set in the environment")
        headers["Authorization"] = f"Bearer {tok}"
    url = (target.get("base_url") or "").rstrip("/") + mapping.get("endpoint", "")
    r = httpx.request(mapping.get("method", "POST"), url, json=payload, headers=headers,
                      timeout=30.0)
    r.raise_for_status()
    try:
        return {"status": r.status_code, "body": r.json()}
    except ValueError:
        return {"status": r.status_code, "body": r.text[:1000]}


# --------------------------------------------------------------------------- #
def plan(sess: Any) -> list[dict[str, Any]]:
    """(target, mapping, document) combinations that have not been dispatched yet."""
    todo = []
    maps = sess.execute(text(
        "SELECT m.*, t.channel, t.base_url, t.auth_ref, t.name AS target_name FROM dispatch_mapping m "
        "JOIN dispatch_target t ON t.id = m.target_id WHERE m.active AND t.enabled")).mappings().all()
    if not maps:
        return []
    docs = [r[0] for r in sess.execute(text(
        "SELECT rp.document_id::text FROM rx_prescription rp ORDER BY rp.synced_at DESC LIMIT 500"))]
    for d in docs:
        if _pending_review(sess, d):
            continue
        rec = record_view(sess, d)
        if not rec or not (rec["medications"] or rec["investigations"] or rec["diagnoses"]):
            continue
        for m in maps:
            payload = apply_mapping(m["mapping"], rec)
            h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
            key = f"{m['target_id']}:{m['screen']}:{d}:{h}"
            exists = sess.execute(text("SELECT 1 FROM dispatch_log WHERE dispatch_key=:k"),
                                  {"k": key}).first()
            if not exists:
                todo.append({"mapping": dict(m), "document_id": d, "payload": payload,
                             "key": key, "patient_mpi": rec["patient"]["mpi_id"]})
    return todo


def run_once() -> list[dict[str, Any]]:
    out = []
    with session_scope() as sess:
        for p in plan(sess):
            m = p["mapping"]
            sess.execute(text(
                "INSERT INTO dispatch_log (target_id, screen, mapping_id, dispatch_key, "
                "document_id, payload) VALUES (:t, :s, :m, :k, :d, CAST(:p AS jsonb)) "
                "ON CONFLICT (dispatch_key) DO NOTHING"),
                {"t": m["target_id"], "s": m["screen"], "m": str(m["id"]), "k": p["key"],
                 "d": p["document_id"], "p": json.dumps(p["payload"], default=str)})
    with session_scope() as sess:
        due = [dict(r) for r in sess.execute(text(
            """
            UPDATE dispatch_log SET attempts = attempts + 1, updated_at = now()
             WHERE id IN (SELECT l.id FROM dispatch_log l JOIN dispatch_target t
                            ON t.id = l.target_id
                           WHERE l.status IN ('pending','failed') AND l.attempts < :max
                             AND t.enabled
                           ORDER BY l.created_at LIMIT 20 FOR UPDATE SKIP LOCKED)
            RETURNING *
            """), {"max": settings.dispatch_max_attempts}).mappings()]
    for row in due:
        with session_scope() as sess:
            t = dict(sess.execute(text("SELECT * FROM dispatch_target WHERE id=:i"),
                                  {"i": row["target_id"]}).mappings().first())
            m = dict(sess.execute(text("SELECT * FROM dispatch_mapping WHERE id=:i"),
                                  {"i": str(row["mapping_id"])}).mappings().first())
        try:
            resp = _send(t, m["mapping"], row["payload"])
            status = "sent" if t["channel"] == "ui" else "acknowledged"
            err = None
        except Exception as exc:  # noqa: BLE001
            resp, err = None, f"{type(exc).__name__}: {exc}"[:1000]
            status = "dead" if row["attempts"] >= settings.dispatch_max_attempts else "failed"
        with session_scope() as sess:
            sess.execute(text("UPDATE dispatch_log SET status=:s, response=CAST(:r AS jsonb), "
                              "last_error=:e, updated_at=now() WHERE id=:i"),
                         {"s": status, "r": json.dumps(resp, default=str) if resp else None,
                          "e": err, "i": str(row["id"])})
        log.info("dispatch", target=row["target_id"], screen=row["screen"],
                 document_id=str(row["document_id"]), status=status, error=err)
        out.append({"id": str(row["id"]), "status": status, "error": err})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="dispatch agent (governed data -> screens)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-target")
    a.add_argument("id"); a.add_argument("name"); a.add_argument("channel", choices=["rest", "ui", "fhir"])
    a.add_argument("base_url", nargs="?"); a.add_argument("auth_ref", nargs="?")
    lm = sub.add_parser("load-mapping")
    lm.add_argument("target"); lm.add_argument("screen"); lm.add_argument("file")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("target"); ap_.add_argument("--by", required=True)
    dis = sub.add_parser("disable"); dis.add_argument("target")
    pv = sub.add_parser("preview")
    pv.add_argument("target"); pv.add_argument("screen"); pv.add_argument("document_id")
    rn = sub.add_parser("run"); rn.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if args.cmd == "run":
        if args.once:
            print(json.dumps(run_once(), indent=2, default=str))
            return
        while True:
            try:
                run_once()
            except Exception as exc:  # noqa: BLE001
                log.error("dispatch_loop_failed", error=str(exc)[:300])
            time.sleep(settings.dispatch_poll_seconds)
    with session_scope() as sess:
        if args.cmd == "add-target":
            sess.execute(text("INSERT INTO dispatch_target (id, name, channel, base_url, auth_ref) "
                              "VALUES (:i, :n, :c, :b, :a) ON CONFLICT (id) DO UPDATE SET "
                              "name=EXCLUDED.name, channel=EXCLUDED.channel, "
                              "base_url=EXCLUDED.base_url, auth_ref=EXCLUDED.auth_ref"),
                         {"i": args.id, "n": args.name, "c": args.channel, "b": args.base_url,
                          "a": args.auth_ref})
            print(f"target {args.id} saved (disabled until approved)")
        elif args.cmd == "load-mapping":
            mp = json.loads(open(args.file, encoding="utf-8").read())
            v = sess.execute(text("SELECT coalesce(max(version),0)+1 FROM dispatch_mapping "
                                  "WHERE target_id=:t AND screen=:s"),
                             {"t": args.target, "s": args.screen}).scalar_one()
            sess.execute(text("UPDATE dispatch_mapping SET active=false WHERE target_id=:t AND screen=:s"),
                         {"t": args.target, "s": args.screen})
            sess.execute(text("INSERT INTO dispatch_mapping (target_id, screen, version, mapping, active) "
                              "VALUES (:t, :s, :v, CAST(:m AS jsonb), true)"),
                         {"t": args.target, "s": args.screen, "v": v, "m": json.dumps(mp)})
            print(f"mapping {args.target}/{args.screen} v{v} active")
        elif args.cmd == "approve":
            sess.execute(text("UPDATE dispatch_target SET enabled=true, approved_by=:b, "
                              "approved_at=now() WHERE id=:i"), {"b": args.by, "i": args.target})
            print(f"target {args.target} approved by {args.by} and enabled")
        elif args.cmd == "disable":
            sess.execute(text("UPDATE dispatch_target SET enabled=false WHERE id=:i"),
                         {"i": args.target})
            print(f"target {args.target} disabled")
        elif args.cmd == "preview":
            m = sess.execute(text("SELECT mapping FROM dispatch_mapping WHERE target_id=:t AND "
                                  "screen=:s AND active"), {"t": args.target, "s": args.screen}).scalar_one()
            rec = record_view(sess, args.document_id)
            print(json.dumps(apply_mapping(m, rec) if rec else {"error": "no rx record"},
                             indent=2, default=str))


if __name__ == "__main__":
    main()
