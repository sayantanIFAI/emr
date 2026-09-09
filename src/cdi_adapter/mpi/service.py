from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..logging import get_logger

log = get_logger(__name__)

_TITLES = re.compile(
    r"^\s*(mr|mrs|ms|miss|master|dr|smt|shri|sri|baby|b/o|s/o|d/o|w/o|c/o)\.?\s+",
    re.I,
)
_NONNAME = re.compile(r"[^a-z\s'-]", re.I)
_WS = re.compile(r"\s+")

_SEX = {
    "m": "M", "male": "M", "boy": "M", "man": "M", "f": "F", "female": "F",
    "girl": "F", "woman": "F", "o": "O", "other": "O", "t": "O", "transgender": "O",
}


def parse_sex(s: Any) -> str | None:
    if not s:
        return None
    return _SEX.get(str(s).strip().lower())


def parse_name(s: Any) -> tuple[str | None, str | None, str | None]:
    """(given, family, full)."""
    if not s:
        return None, None, None
    raw = str(s).strip()
    prev = None
    while prev != raw:  # strip stacked titles ("Dr Mr X")
        prev = raw
        raw = _TITLES.sub("", raw).strip()
    raw = _NONNAME.sub(" ", raw)
    raw = _WS.sub(" ", raw).strip()
    raw = " ".join(w.capitalize() for w in raw.split())
    if not raw:
        return None, None, None
    parts = raw.split(" ")
    if len(parts) == 1:
        return parts[0], None, parts[0]
    return parts[0], " ".join(parts[1:]), raw


def name_key(full: str | None) -> str:
    if not full:
        return ""
    return _WS.sub(" ", _NONNAME.sub(" ", full.lower())).strip()


def names_match(a: str | None, b: str | None, cutoff: float = 0.72) -> bool:
    """Loose match: same person written slightly differently. Also treats one name
    as a subset of the other (e.g. 'Onkar Choudhury' vs 'Mr Onkar Choudhury')."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return True  # nothing to compare -> don't block
    if ka == kb:
        return True
    sa, sb = set(ka.split()), set(kb.split())
    if sa and sb and (sa <= sb or sb <= sa):
        return True
    common = sa & sb
    if len(common) >= 2:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= cutoff


def dob_match(a: date | None, b: date | None) -> bool:
    if not a or not b:
        return True
    return abs((a - b).days) <= 366  # within a year (age-derived DOBs are approximate)


def parse_age(age_text: Any, ref: datetime | None = None) -> tuple[int | None, date | None]:
    """('45 Y' | '45' | '6 M' | '45yrs') -> (age_years, approx birth_date)."""
    if age_text is None:
        return None, None
    s = str(age_text).strip().lower()
    ref = ref or datetime.now(timezone.utc)
    m = re.search(r"(\d+)\s*(y|yr|yrs|year|years)?\b", s)
    if m and (not m.group(2) or m.group(2).startswith("y") or m.group(2).startswith("year")):
        yrs = int(m.group(1))
        if 0 <= yrs <= 120:
            return yrs, date(ref.year - yrs, 1, 1)
    m = re.search(r"(\d+)\s*(m|mo|month|months)\b", s)
    if m:
        return 0, date(ref.year, 1, 1)
    m = re.fullmatch(r"\s*(\d{1,3})\s*", s)
    if m:
        yrs = int(m.group(1))
        if 0 <= yrs <= 120:
            return yrs, date(ref.year - yrs, 1, 1)
    return None, None


def parse_dob(s: Any) -> date | None:
    if not s:
        return None
    from ..extract.service import _parse_date  # reuse the doc-date parser

    dt, _prec = _parse_date(str(s))
    return dt.date() if dt else None


@dataclass
class IdentityCandidate:
    name_full: str | None = None
    name_given: str | None = None
    name_family: str | None = None
    sex: str | None = None
    age_years: int | None = None
    birth_date: date | None = None
    abha: str | None = None
    source_doc_id: str | None = None
    confidence: float = 0.6


@dataclass
class IdentityResult:
    patient_id: str
    mpi_id: str
    created: bool
    name_full: str | None
    sex: str | None
    birth_date: date | None
    match_status: str


def candidate_from_payload(payload: dict[str, Any], *, abha_hint: str | None,
                           source_doc_id: str | None) -> IdentityCandidate:
    p = payload.get("patient") or {}
    given, family, full = parse_name(p.get("name"))
    age_years, bd_from_age = parse_age(p.get("age_text"))
    bd = parse_dob(p.get("dob") or p.get("birth_date")) or bd_from_age
    return IdentityCandidate(
        name_full=full, name_given=given, name_family=family,
        sex=parse_sex(p.get("sex")),
        age_years=age_years, birth_date=bd,
        abha=(abha_hint or None),
        source_doc_id=source_doc_id,
        confidence=float(payload.get("extracted_at_confidence") or 0.6),
    )


def _next_mpi(sess: Session) -> str:
    seq = sess.execute(text("SELECT nextval('patient_mpi_seq')")).scalar_one()
    return f"CFP-{datetime.now(timezone.utc).year}-{int(seq):06d}"


def resolve_identity(sess: Session, cand: IdentityCandidate) -> IdentityResult:
    """Find or create the patient this document belongs to."""
    # 1. ABHA is authoritative
    if cand.abha:
        row = sess.execute(
            text("SELECT id, mpi_id, name_full, gender, birth_date FROM patient_identity "
                 "WHERE abha_number = :a"),
            {"a": cand.abha},
        ).mappings().first()
        if row:
            return IdentityResult(str(row["id"]), row["mpi_id"], False, row["name_full"],
                                  row["gender"], row["birth_date"], "abha_verified")

    # 2. fuzzy: same-ish name + birth year within 1
    key = name_key(cand.name_full)
    by = cand.birth_date.year if cand.birth_date else None
    if key:
        rows = sess.execute(
            text("SELECT id, mpi_id, name_full, gender, birth_date FROM patient_identity")
        ).mappings().all()
        best, best_score = None, 0.0
        for r in rows:
            rk = name_key(r["name_full"])
            if not rk:
                continue
            s = difflib.SequenceMatcher(None, key, rk).ratio()
            ry = r["birth_date"].year if r["birth_date"] else None
            if by and ry and abs(by - ry) > 1:
                s -= 0.3
            if s > best_score:
                best, best_score = r, s
        if best is not None and best_score >= 0.9:
            return IdentityResult(str(best["id"]), best["mpi_id"], False, best["name_full"],
                                  best["gender"], best["birth_date"], "auto")

    # 3. new patient
    mpi = _next_mpi(sess)
    pid = sess.execute(
        text(
            """
            INSERT INTO patient_identity
              (mpi_id, abha_number, name_given, name_family, name_full, gender,
               birth_date, birth_date_est, age_years, match_status, match_score,
               identity_confidence)
            VALUES (:mpi, CAST(:abha AS text), CAST(:g AS text), CAST(:f AS text),
                    CAST(:full AS text), CAST(:sex AS text), CAST(:bd AS date),
                    :bd_est, :age, :ms, :msc, :ic)
            RETURNING id
            """
        ),
        {
            "mpi": mpi, "abha": cand.abha, "g": cand.name_given, "f": cand.name_family,
            "full": cand.name_full, "sex": cand.sex, "bd": cand.birth_date,
            "bd_est": cand.birth_date is not None and cand.age_years is not None,
            "age": cand.age_years,
            "ms": "abha_verified" if cand.abha else "auto",
            "msc": 1.0 if cand.abha else 0.75,
            "ic": round(cand.confidence, 3),
        },
    ).scalar_one()
    log.info("mpi_new_patient", mpi_id=mpi, name=cand.name_full, sex=cand.sex)
    return IdentityResult(str(pid), mpi, True, cand.name_full, cand.sex,
                          cand.birth_date, "abha_verified" if cand.abha else "auto")


def record_alias(sess: Session, patient_id: str, cand: IdentityCandidate) -> None:
    sess.execute(
        text(
            """
            INSERT INTO patient_identity_alias
              (patient_id, document_id, raw_name, raw_dob, raw_age)
            VALUES (:p, CAST(:d AS uuid), :n, :dob, :age)
            """
        ),
        {"p": patient_id, "d": cand.source_doc_id, "n": cand.name_full,
         "dob": cand.birth_date.isoformat() if cand.birth_date else None,
         "age": str(cand.age_years) if cand.age_years is not None else None},
    )


def merge_identity_evidence(sess: Session, patient_id: str,
                            cands: list[IdentityCandidate]) -> dict[str, Any]:
    """After all documents: pick the best-supported name / sex / DOB and finalise."""
    cur = sess.execute(
        text("SELECT * FROM patient_identity WHERE id = :i"), {"i": patient_id}
    ).mappings().first()
    if not cur:
        return {}

    names = [c.name_full for c in cands if c.name_full]
    sexes = [c.sex for c in cands if c.sex]
    dobs = [c.birth_date for c in cands if c.birth_date]
    ages = [c.age_years for c in cands if c.age_years is not None]

    def _mode(xs: list[Any]) -> Any | None:
        if not xs:
            return None
        c = Counter(xs)
        top = c.most_common()
        best = max(top, key=lambda kv: (kv[1], len(str(kv[0]))))
        return best[0]

    name_full = _mode(names) or cur["name_full"]
    given, family, full = parse_name(name_full) if name_full else (
        cur["name_given"], cur["name_family"], cur["name_full"])
    sex = _mode(sexes) or cur["gender"]
    dob = _mode(dobs) or cur["birth_date"]
    age = _mode(ages) if ages else cur["age_years"]

    agree = 0
    if names:
        agree += sum(1 for n in names if name_key(n) == name_key(full)) / len(names)
    if sexes:
        agree += sum(1 for s in sexes if s == sex) / len(sexes)
    ident_conf = round(min(0.99, 0.5 + 0.25 * agree), 3)

    sess.execute(
        text(
            """
            UPDATE patient_identity
               SET name_given = CAST(:g AS text), name_family = CAST(:f AS text),
                   name_full = CAST(:full AS text), gender = CAST(:sex AS text),
                   birth_date = CAST(:bd AS date), age_years = :age,
                   identity_confidence = :ic, updated_at = now()
             WHERE id = :i
            """
        ),
        {"g": given, "f": family, "full": full, "sex": sex, "bd": dob,
         "age": age, "ic": ident_conf, "i": patient_id},
    )
    out = {"mpi_id": cur["mpi_id"], "name": full, "sex": sex,
           "birth_date": dob.isoformat() if dob else None,
           "age_years": age, "abha_number": cur["abha_number"],
           "identity_confidence": ident_conf}
    log.info("mpi_merged", patient_id=patient_id, **{k: out[k] for k in ("mpi_id", "name", "sex")})
    return out
