"""The clinic's patient master (``patient_registry``).

- ``lookup`` — resolve an existing patient by CareFlow id, ABHA id or mobile.
- ``ensure_identity`` — get (or create) the ``patient_identity`` row for a
  registry match, so the pipeline attaches every document to that patient and
  skips document-driven identity resolution.
- ``save`` — after a NEW patient's documents are processed, persist the full
  details entered by the operator for next time.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..logging import get_logger
from .service import parse_name, parse_sex

log = get_logger(__name__)

_DIGITS = re.compile(r"\D+")


def _row(r: Any) -> dict[str, Any] | None:
    if not r:
        return None
    d = dict(r)
    if d.get("dob"):
        d["dob"] = str(d["dob"])[:10]
    return d


def lookup(sess: Session, q: str) -> dict[str, Any] | None:
    q = (q or "").strip()
    if not q:
        return None
    digits = _DIGITS.sub("", q)
    row = sess.execute(
        text(
            """
            SELECT * FROM patient_registry
             WHERE patient_id = :q
                OR abha_id = :q
                OR replace(abha_id, '-', '') = :d
                OR regexp_replace(mobile, '\\D', '', 'g') = :d
             ORDER BY updated_at DESC
             LIMIT 1
            """
        ),
        {"q": q, "d": digits or "\x00"},
    ).mappings().first()
    return _row(row)


def _next_mpi(sess: Session) -> str:
    seq = sess.execute(text("SELECT nextval('patient_mpi_seq')")).scalar_one()
    return f"CFP-{datetime.now(timezone.utc).year}-{int(seq):06d}"


def ensure_identity(sess: Session, reg: dict[str, Any]) -> tuple[str, str]:
    """Return (patient_identity.id, mpi_id) for a registry match, creating the
    identity row if the clinic has this person on file but they've not been
    processed here yet."""
    row = None
    if reg.get("patient_id"):
        row = sess.execute(
            text("SELECT id, mpi_id FROM patient_identity WHERE mpi_id = :m"),
            {"m": reg["patient_id"]},
        ).mappings().first()
    if not row and reg.get("abha_id"):
        row = sess.execute(
            text("SELECT id, mpi_id FROM patient_identity WHERE abha_number = :a"),
            {"a": reg["abha_id"]},
        ).mappings().first()
    if row:
        return str(row["id"]), row["mpi_id"]

    mpi = reg.get("patient_id") or _next_mpi(sess)
    given, family, full = parse_name(reg.get("name"))
    pid = sess.execute(
        text(
            """
            INSERT INTO patient_identity
              (mpi_id, abha_number, name_given, name_family, name_full, gender,
               birth_date, match_status, match_score, identity_confidence)
            VALUES (:mpi, CAST(:abha AS text), CAST(:g AS text), CAST(:f AS text),
                    CAST(:full AS text), CAST(:sex AS text), CAST(:bd AS date),
                    'clerk_confirmed', 1.0, 1.0)
            RETURNING id
            """
        ),
        {"mpi": mpi, "abha": reg.get("abha_id"), "g": given, "f": family, "full": full,
         "sex": parse_sex(reg.get("gender")) or reg.get("gender"), "bd": reg.get("dob")},
    ).scalar_one()
    # link the CareFlow id back onto the registry row
    sess.execute(
        text("UPDATE patient_registry SET patient_id = :m, updated_at = now() WHERE id = :i"),
        {"m": mpi, "i": reg["id"]},
    )
    log.info("registry_identity_created", mpi_id=mpi, name=full)
    return str(pid), mpi


def save(sess: Session, *, patient_id: str, name: str, mobile: str,
         dob: str | None, gender: str | None, address: str | None,
         abha_id: str | None) -> dict[str, Any]:
    """Upsert the full patient record entered for a new patient."""
    row = sess.execute(
        text(
            """
            INSERT INTO patient_registry (patient_id, name, mobile, dob, gender, address, abha_id)
            VALUES (:pid, :name, :mobile, CAST(:dob AS date), CAST(:gender AS text),
                    CAST(:address AS text), CAST(:abha AS text))
            ON CONFLICT (mobile, lower(name), COALESCE(dob, DATE '1900-01-01'))
            DO UPDATE SET patient_id = EXCLUDED.patient_id,
                          gender = COALESCE(EXCLUDED.gender, patient_registry.gender),
                          address = COALESCE(EXCLUDED.address, patient_registry.address),
                          abha_id = COALESCE(EXCLUDED.abha_id, patient_registry.abha_id),
                          updated_at = now()
            RETURNING *
            """
        ),
        {"pid": patient_id, "name": name.strip(), "mobile": mobile.strip(),
         "dob": dob or None, "gender": gender or None, "address": address or None,
         "abha": abha_id or None},
    ).mappings().first()
    # mirror onto the identity row
    if abha_id:
        sess.execute(
            text("UPDATE patient_identity SET abha_number = COALESCE(abha_number, :a), "
                 "updated_at = now() WHERE mpi_id = :m"),
            {"a": abha_id, "m": patient_id},
        )
    log.info("registry_saved", patient_id=patient_id, mobile=mobile)
    return _row(row) or {}
