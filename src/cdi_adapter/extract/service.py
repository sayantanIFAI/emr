from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from ..ml.client import MLError, get_client
from .prompt import block_id_map, build_extraction_prompt, load_schema

log = get_logger(__name__)

_MODEL_STACK = {
    "classifier": "mlserve/qwen2.5-vl",
    "ocr": "rapidocr+vlm",
    "extractor": "mlserve/qwen2.5-vl",
    "terminology": "seed-v1",
}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_date(s: str | None) -> tuple[datetime | None, str]:
    if not s:
        return None, "day"
    s = s.strip()
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$", s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        prec = "year" if not mo else "month" if not d else "day"
        return datetime(int(y), int(mo or 1), int(d or 1), tzinfo=timezone.utc), prec
    m = re.match(r"^(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{2,4})$", s)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
        if y < 100:
            y += 2000
        if mon in _MONTHS:
            return datetime(y, _MONTHS[mon], d, tzinfo=timezone.utc), "day"
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc), "day"
        except ValueError:
            return None, "day"
    return None, "day"


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, (dict, list, bool)):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"-?\d+(?:\.\d+)?", str(x))
    return float(m.group(0)) if m else None


def _asdict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


_UNIT_RE = re.compile(r"-?\d+(?:\.\d+)?\s*([A-Za-z%/µ][A-Za-z%/0-9µ.\[\]*^-]*)")


def _qty(x: Any) -> tuple[float | None, str | None, list[str] | None]:
    """Read a value/unit/evidence out of a quantity-ish field that the model may
    have emitted as an object, a bare number, or a string like '500 mg'."""
    if isinstance(x, dict):
        return (_num(x.get("value")),
                x.get("unit_ucum") or x.get("unit_text") or x.get("unit"),
                x.get("evidence"))
    if isinstance(x, (int, float)):
        return float(x), None, None
    if isinstance(x, str):
        m = _UNIT_RE.search(x)
        return _num(x), (m.group(1) if m else None), None
    return None, None, None


@dataclass
class ExtractResult:
    document_id: str
    schema: str
    n_facts: int
    skipped: bool = False
    patient_id: str | None = None
    mpi_id: str | None = None
    identity: dict[str, Any] | None = None   # raw candidate read from THIS doc


class _Ctx:
    def __init__(self, sess, doc_id, patient_id, encounter_id, extraction_id,
                 page_id, blkmap, blocks_by_id, run_ids):
        self.sess = sess
        self.doc_id = doc_id
        self.patient_id = patient_id
        self.encounter_id = encounter_id
        self.extraction_id = extraction_id
        self.page_id = page_id
        self.blkmap = blkmap
        self.blocks_by_id = blocks_by_id
        self.run_ids = run_ids
        self.n = 0

    def add(self, *, fact_type: str, local_text: str, evidence: list[str] | None = None,
            extracted_text: str | None = None, **cols: Any) -> UUID:
        cols.setdefault("confidence_extract", self._conf)
        cols.setdefault("confidence_overall", round(0.5 * (self._conf + self._ocr_conf(evidence)), 3))
        cols.setdefault("confidence_ocr", self._ocr_conf(evidence))
        fid = repo.insert_clinical_fact(
            self.sess, patient_id=self.patient_id, encounter_id=self.encounter_id,
            fact_type=fact_type, local_text=(local_text or "")[:2000],
            extraction_id=self.extraction_id, source_doc_ids=[self.doc_id], **cols,
        )
        ids = [self.blkmap[e] for e in (evidence or []) if e in self.blkmap]
        bbox = self._bbox_union(ids)
        repo.insert_fact_provenance(
            self.sess, fact_id=fid, source_doc_id=self.doc_id, page_id=self.page_id,
            ocr_block_ids=ids, bbox_union=bbox,
            extracted_text=extracted_text or local_text or "",
            pipeline_run_ids=self.run_ids, model_stack=_MODEL_STACK,
        )
        self.n += 1
        return fid

    _conf = 0.8

    def _ocr_conf(self, evidence: list[str] | None) -> float:
        ids = [self.blkmap[e] for e in (evidence or []) if e in self.blkmap]
        vals = [float(self.blocks_by_id[i]["ocr_conf"]) for i in ids if i in self.blocks_by_id]
        return round(sum(vals) / len(vals), 3) if vals else 0.5

    def _bbox_union(self, ids: list[str]) -> list[int] | None:
        boxes = [self.blocks_by_id[i]["bbox"] for i in ids if i in self.blocks_by_id]
        if not boxes:
            return None
        return [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)]


# --------------------------------------------------------------------------- #
# per-doc-type payload -> facts
# --------------------------------------------------------------------------- #
def _coded_text(x: Any) -> tuple[str, list[str] | None]:
    if isinstance(x, dict):
        return (x.get("text") or x.get("code") or x.get("display") or x.get("name") or "",
                x.get("evidence"))
    if isinstance(x, str):
        return x, None
    return "", None


def _facts_prescription(c: _Ctx, p: dict[str, Any]) -> None:
    for dx in p.get("diagnoses") or []:
        t, ev = _coded_text(dx)
        if t:
            c.add(fact_type="condition", local_text=t, value_code_display=t, evidence=ev,
                  clinical_status="active", verification="confirmed")
    for m in p.get("medications") or []:
        _add_medication(c, m, intent="order",
                        status=m.get("status") if isinstance(m, dict) else None)
    for a in p.get("advice") or []:
        t, ev = _coded_text(a)
        if t:
            c.add(fact_type="advice", local_text=t, value_text=t, evidence=ev)
    _add_vitals(c, p.get("vitals") or [])


_facts_opd_note = _facts_prescription


def _facts_lab(c: _Ctx, p: dict[str, Any]) -> None:
    coll, prec = _parse_date(p.get("reported_at") or p.get("collected_at"))
    for r in p.get("results") or []:
        if not isinstance(r, dict):
            continue
        val, unit, _ev = _qty(r.get("value"))
        c.add(
            fact_type="lab_result",
            local_text=r.get("analyte_text") or r.get("text") or "",
            value_code_display=r.get("analyte_text") or r.get("text"),
            value_kind="quantity" if val is not None else "string",
            value_num=val,
            value_unit_ucum=unit or r.get("unit"),
            value_text=r.get("value_text"),
            ref_range_low=_num(r.get("ref_low")), ref_range_high=_num(r.get("ref_high")),
            ref_range_text=r.get("ref_text"),
            abnormal_flag=r.get("flag"),
            effective_time=coll, effective_precision=prec,
            evidence=r.get("evidence"),
        )


def _facts_vitals(c: _Ctx, p: dict[str, Any]) -> None:
    _add_vitals(c, p.get("vitals") or [])
    for s in p.get("symptoms") or []:
        t, ev = _coded_text(s)
        if t:
            c.add(fact_type="symptom", local_text=t, value_code_display=t, evidence=ev)
    for a in p.get("allergies") or []:
        if isinstance(a, str):
            a = {"substance_text": a}
        elif not isinstance(a, dict):
            continue
        sub = a.get("substance_text") or a.get("text") or a.get("name") or ""
        if sub:
            c.add(fact_type="allergy", local_text=sub, value_code_display=sub,
                  value_text=a.get("reaction_text"), evidence=a.get("evidence"),
                  clinical_status="active", verification="unconfirmed")


def _facts_discharge(c: _Ctx, p: dict[str, Any]) -> None:
    adm, _ = _parse_date(p.get("admission_date"))
    for dx in p.get("diagnoses") or []:
        t, ev = _coded_text(dx)
        if t:
            c.add(fact_type="condition", local_text=t, value_code_display=t, evidence=ev,
                  clinical_status="active", verification="confirmed", onset=adm)
    for pr in p.get("procedures") or []:
        if isinstance(pr, str):
            pr = {"name": pr}
        elif not isinstance(pr, dict):
            continue
        nm = pr.get("name") or pr.get("text") or ""
        pdt, pprec = _parse_date(pr.get("date"))
        if nm:
            c.add(fact_type="procedure", local_text=nm, value_code_display=nm,
                  evidence=pr.get("evidence"), effective_time=pdt,
                  effective_precision=pprec, value_text=pr.get("findings"),
                  clinical_status="completed")
    for m in p.get("medications_on_discharge") or []:
        _add_medication(c, m, intent="order")


def _facts_radiology(c: _Ctx, p: dict[str, Any]) -> None:
    sd, prec = _parse_date(p.get("study_date"))
    concl = p.get("impression") or p.get("findings") or ""
    concepts = p.get("impression_concepts") or []
    first_ev = concepts[0].get("evidence") if concepts and isinstance(concepts[0], dict) else None
    c.add(fact_type="diagnostic_report",
          local_text=(p.get("study_name") or p.get("modality") or "Imaging study"),
          value_text=concl if isinstance(concl, str) else str(concl),
          effective_time=sd, effective_precision=prec, evidence=first_ev)
    for concept in concepts:
        t, ev = _coded_text(concept)
        if t:
            c.add(fact_type="finding", local_text=t, value_code_display=t,
                  evidence=ev, effective_time=sd)


_VITAL_ALIAS = {
    "pulse": "heart_rate", "hr": "heart_rate", "heart_rate": "heart_rate",
    "resp": "resp_rate", "rr": "resp_rate", "respiratory_rate": "resp_rate",
    "temp": "temperature", "temperature": "temperature",
    "spo2": "spo2", "o2_sat": "spo2", "oxygen_saturation": "spo2",
    "weight": "weight", "wt": "weight", "height": "height", "ht": "height",
    "bmi": "bmi", "pain_score": "pain_score", "pain": "pain_score",
    "systolic_bp": "systolic_bp", "diastolic_bp": "diastolic_bp",
}


def _add_vitals(c: _Ctx, vitals: list[Any]) -> None:
    seen: set[str] = set()

    def put(name: str, val: float | None, unit: str | None, ev: list[str] | None) -> None:
        if val is None or name in seen:
            return
        seen.add(name)
        c.add(fact_type="vital_sign", local_text=name.replace("_", " "),
              value_code_display=name, value_kind="quantity",
              value_num=val, value_unit_ucum=unit, evidence=ev)

    for v in vitals:
        if not isinstance(v, dict):
            continue
        raw = (v.get("name") or "").strip().lower().replace(" ", "_")
        name = _VITAL_ALIAS.get(raw, raw)
        ev = v.get("evidence")
        if v.get("systolic") is not None or v.get("diastolic") is not None:
            put("systolic_bp", _num(v.get("systolic")), "mm[Hg]", ev)
            put("diastolic_bp", _num(v.get("diastolic")), "mm[Hg]", ev)
            continue
        val, unit, _ev = _qty(v.get("value"))
        vstr = v.get("value") if isinstance(v.get("value"), str) else None
        if vstr and (m := re.match(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", vstr)):
            put("systolic_bp", float(m.group(1)), "mm[Hg]", ev)
            put("diastolic_bp", float(m.group(2)), "mm[Hg]", ev)
            continue
        put(name or "vital", val, unit, ev)


def _add_medication(c: _Ctx, m: Any, *, intent: str, status: str | None = None) -> None:
    if not isinstance(m, dict):
        if isinstance(m, str):
            m = {"drug_text": m, "evidence": []}
        else:
            return
    drug = m.get("drug_text") or m.get("text") or m.get("name") or ""
    s_val, s_unit, _ = _qty(m.get("strength"))
    d_val, d_unit, _ = _qty(m.get("dose"))
    dur = m.get("duration_days")
    cs = {"stopped": "stopped", "changed": "active"}.get(status or "", "active")
    fid = c.add(
        fact_type="medication", local_text=drug, value_code_display=drug,
        evidence=m.get("evidence"), clinical_status=cs, verification="confirmed",
    )
    repo.insert_medication_detail(
        c.sess, fid,
        drug_text=drug,
        form=m.get("form") if isinstance(m.get("form"), str) else None,
        strength_num=s_val, strength_unit=s_unit,
        dose_num=d_val, dose_unit_ucum=d_unit,
        route=m.get("route") if isinstance(m.get("route"), str) else None,
        frequency_code=m.get("frequency_text") or m.get("frequency"),
        duration_days=int(dur) if isinstance(dur, (int, float)) else _num(dur) and int(_num(dur)),
        prn=m.get("prn") if isinstance(m.get("prn"), bool) else None,
        instructions=m.get("instructions") if isinstance(m.get("instructions"), str) else None,
        intent=intent,
    )


_HANDLERS = {
    "cdi:prescription.v3": _facts_prescription,
    "cdi:opd_note.v3": _facts_opd_note,
    "cdi:lab_report.v3": _facts_lab,
    "cdi:vitals.v3": _facts_vitals,
    "cdi:discharge_summary.v3": _facts_discharge,
    "cdi:radiology.v3": _facts_radiology,
}


# --------------------------------------------------------------------------- #
def extract_document(document_id: str, *, patient_id: str | None = None,
                     encounter_id: str | None = None,
                     abha_hint: str | None = None) -> ExtractResult:
    client = get_client()
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise ValueError(f"document {document_id} not found")
        cls = repo.get_doc_classification(sess, document_id)
        if not cls:
            raise ValueError(f"document {document_id} not classified")
        pages = repo.list_document_pages(sess, document_id)
        blocks = repo.list_ocr_blocks(sess, document_id)
        run_id = repo.start_pipeline_run(
            sess, document_id=document_id, stage="extract",
            model_name="mlserve/vlm", params={"doc_type": cls["doc_type"]},
        )
        run_ids = [str(r["id"]) for r in sess.execute(
            __import__("sqlalchemy").text(
                "SELECT id FROM pipeline_run WHERE document_id=:d"), {"d": document_id}
        ).mappings().all()]

    loaded = load_schema(cls["doc_type"])
    if not loaded:
        with session_scope() as sess:
            repo.finish_pipeline_run(sess, run_id, status="skipped",
                                     metrics={"reason": f"no schema for {cls['doc_type']}"})
            repo.set_document_status(sess, document_id, "normalized")
        log.info("extract_no_schema", document_id=document_id, doc_type=cls["doc_type"])
        return ExtractResult(document_id, cls["doc_type"], 0, skipped=True)

    schema_id, schema = loaded
    prompt = build_extraction_prompt(cls["doc_type"], blocks)
    blkmap = block_id_map(blocks)
    image = storage.get_bytes(storage.key_from_uri(pages[0]["image_uri"]))

    try:
        payload = client.vlm_json(image, prompt, schema,
                                  max_tokens=settings.extract_max_tokens,
                                  retries=settings.extract_retries)
    except MLError as exc:
        with session_scope() as sess:
            repo.finish_pipeline_run(sess, run_id, status="failed", error_detail=str(exc)[:400])
            repo.set_document_status(sess, document_id, "error", error_detail=f"extract: {exc}")
        raise

    blocks_by_id = {str(b["id"]): b for b in blocks}
    handler = _HANDLERS.get(schema_id)

    from ..mpi.service import (candidate_from_payload, merge_identity_evidence,
                               record_alias, resolve_identity)

    with session_scope() as sess:
        # ---- identity: read name / sex / age from THIS document ----
        cand = candidate_from_payload(payload, abha_hint=abha_hint, source_doc_id=document_id)
        mpi_id = None
        if patient_id:
            pid = patient_id
            record_alias(sess, pid, cand)
            row = sess.execute(
                __import__("sqlalchemy").text(
                    "SELECT mpi_id FROM patient_identity WHERE id = :i"), {"i": pid}
            ).first()
            mpi_id = row[0] if row else None
        else:
            res = resolve_identity(sess, cand)
            pid, mpi_id = res.patient_id, res.mpi_id
            record_alias(sess, pid, cand)
        identity_out = {
            "name": cand.name_full, "sex": cand.sex, "age_years": cand.age_years,
            "birth_date": cand.birth_date.isoformat() if cand.birth_date else None,
            "source_document_id": document_id,
        }
        eid = encounter_id
        if not eid:
            edate, eprec = _parse_date(
                payload.get("encounter_date") or payload.get("reported_at")
                or payload.get("study_date") or payload.get("discharge_date")
                or payload.get("recorded_at")
            )
            enc_class = "IMP" if cls["doc_type"] in ("discharge_summary", "operative_note") else "AMB"
            eid = str(repo.create_encounter(
                sess, patient_id=pid, enc_class=enc_class,
                period_start=edate or doc.get("captured_at") or doc["ingested_at"],
                period_end=None, period_precision=eprec, specialty=cls.get("specialty"),
                derived_from=[document_id], confidence=float(cls["confidence"]),
            ))

        ext_id = repo.insert_extraction(
            sess, document_id=document_id, schema_name=schema_id,
            schema_version="v3", payload=payload,
            evidence_map={"block_ids": blkmap}, model_run_id=run_id,
        )

        ctx = _Ctx(sess, document_id, pid, eid, ext_id, pages[0]["id"],
                   blkmap, blocks_by_id, run_ids)
        ctx._conf = float(payload.get("extracted_at_confidence") or 0.75)
        if handler:
            handler(ctx, payload)

        repo.finish_pipeline_run(sess, run_id, status="ok",
                                 metrics={"facts": ctx.n, "schema": schema_id})
        repo.set_document_status(sess, document_id, "extracted")
        repo.write_audit(sess, actor="extract-svc", action="create", entity="clinical_fact",
                         entity_id=document_id, patient_id=pid,
                         detail={"facts": ctx.n, "doc_type": cls["doc_type"]})

    log.info("extracted", document_id=document_id, doc_type=cls["doc_type"], facts=ctx.n,
             patient=identity_out.get("name"), mpi_id=mpi_id)
    return ExtractResult(document_id, cls["doc_type"], ctx.n, patient_id=pid,
                         mpi_id=mpi_id, identity=identity_out)
