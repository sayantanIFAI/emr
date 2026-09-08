from __future__ import annotations

import difflib
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text

from ..db import session_scope
from ..logging import get_logger
from . import seed

log = get_logger(__name__)

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("hba1c", "hba1c").replace("hbalc", "hba1c").replace("hba lc", "hba1c")
    s = re.sub(r"[^\w%/.\- ]", " ", s)
    return _WS.sub(" ", s).strip()


def _fuzzy(key: str, table: dict[str, Any]) -> Any | None:
    m = difflib.get_close_matches(key, list(table), n=1, cutoff=0.86)
    return table[m[0]] if m else None


def to_ucum(unit_text: str | None) -> str | None:
    if not unit_text:
        return None
    u = unit_text.strip().lower()
    return seed.UCUM_ALIASES.get(u, unit_text.strip())


def freq_per_day(freq_text: str | None) -> float | None:
    if not freq_text:
        return None
    f = _norm(freq_text)
    for k, v in seed.FREQ_PER_DAY.items():
        if k in f:
            return float(v)
    m = re.match(r"^\s*(\d)-(\d)-(\d)(?:-(\d))?\s*$", freq_text.strip())
    if m:
        return float(sum(int(x) for x in m.groups() if x))
    return None


def bind_fact(fact: dict[str, Any]) -> dict[str, Any]:
    """Return a dict of column updates for one clinical_fact row."""
    ft = fact["fact_type"]
    keyraw = fact.get("value_code_display") or fact["local_text"] or ""
    key = _norm(keyraw)
    up: dict[str, Any] = {}

    if ft in ("condition",):
        hit = seed.CONDITIONS.get(key) or _fuzzy(key, seed.CONDITIONS)
        if hit:
            sysu, code, disp, icd = hit
            up.update(code_system=sysu, code=code, code_display=disp, code_status="bound")
    elif ft in ("symptom", "finding"):
        hit = seed.SYMPTOMS.get(key) or _fuzzy(key, seed.SYMPTOMS)
        if hit:
            up.update(code_system=hit[0], code=hit[1], code_display=hit[2], code_status="bound")
    elif ft == "procedure":
        hit = seed.PROCEDURES.get(key) or _fuzzy(key, seed.PROCEDURES)
        if hit:
            up.update(code_system=hit[0], code=hit[1], code_display=hit[2], code_status="bound")
    elif ft == "lab_result":
        hit = seed.LABS.get(key) or _fuzzy(key, seed.LABS)
        if hit:
            sysu, code, disp, unit = hit
            up.update(code_system=sysu, code=code, code_display=disp, code_status="bound")
            if not fact.get("value_unit_ucum") and unit:
                up["value_unit_ucum"] = unit
        if fact.get("value_unit_ucum"):
            up["value_unit_ucum"] = to_ucum(fact["value_unit_ucum"])
    elif ft == "vital_sign":
        hit = seed.VITALS.get(key) or _fuzzy(key, seed.VITALS)
        if hit:
            up.update(code_system=hit[0], code=hit[1], code_display=hit[2], code_status="bound")
            if not fact.get("value_unit_ucum"):
                up["value_unit_ucum"] = hit[3]
        if fact.get("value_unit_ucum"):
            up["value_unit_ucum"] = to_ucum(fact["value_unit_ucum"])
    elif ft == "medication":
        # strip leading dose-form words, then match any token against the drug map
        toks = [_norm(t) for t in re.split(r"[\s,]+", keyraw) if t]
        toks = [t for t in toks if t not in
                ("tab", "tab.", "cap", "cap.", "tablet", "capsule", "syp", "syrup",
                 "inj", "injection", "susp", "cream", "oint")]
        hit = None
        for t in toks:
            hit = seed.DRUGS.get(t) or _fuzzy(t, seed.DRUGS)
            if hit:
                break
        hit = hit or seed.DRUGS.get(key) or _fuzzy(key, seed.DRUGS)
        if hit:
            up.update(code_system=hit[0], code=hit[1], code_display=hit[2], code_status="bound")
    elif ft == "allergy":
        hit = seed.ALLERGENS.get(key) or _fuzzy(key, seed.ALLERGENS)
        if hit:
            up.update(code_system=hit[0], code=hit[1], code_display=hit[2], code_status="bound")

    if not up:
        up["code_status"] = "local_only"
    # recompute terminology confidence
    up["confidence_terminology"] = 0.9 if up.get("code_status") == "bound" else 0.4
    return up


def bind_document(document_id: str | UUID) -> dict[str, int]:
    """Bind every clinical_fact derived from this document. Returns counts."""
    bound = local = 0
    with session_scope() as sess:
        rows = sess.execute(
            text(
                """
                SELECT * FROM clinical_fact
                 WHERE :doc = ANY(source_doc_ids) AND is_current
                """
            ),
            {"doc": str(document_id)},
        ).mappings().all()

        for fact in rows:
            up = bind_fact(dict(fact))
            sets = ", ".join(f"{k} = :{k}" for k in up)
            sess.execute(
                text(f"UPDATE clinical_fact SET {sets} WHERE id = :id"),
                {**up, "id": str(fact["id"])},
            )
            # medication frequency / dose-form derived fields
            if fact["fact_type"] == "medication":
                md = sess.execute(
                    text("SELECT * FROM medication_detail WHERE fact_id = :id"),
                    {"id": str(fact["id"])},
                ).mappings().first()
                if md:
                    fpd = md["frequency_per_day"] or freq_per_day(md["frequency_code"])
                    sess.execute(
                        text(
                            "UPDATE medication_detail SET frequency_per_day = :f, "
                            "dose_unit_ucum = :u WHERE fact_id = :id"
                        ),
                        {
                            "f": fpd,
                            "u": to_ucum(md["dose_unit_ucum"] or md["strength_unit"]),
                            "id": str(fact["id"]),
                        },
                    )
            if up.get("code_status") == "bound":
                bound += 1
            else:
                local += 1

        sess.execute(
            text(
                "UPDATE source_document SET status = 'normalized' "
                "WHERE id = :id AND status NOT IN ('projected','error')"
            ),
            {"id": str(document_id)},
        )
    log.info("terminology_bound", document_id=str(document_id), bound=bound, local_only=local)
    return {"bound": bound, "local_only": local}
