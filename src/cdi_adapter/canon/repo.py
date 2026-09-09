"""Insert helpers for the canonical (cn_*) tables + provenance + audit.

Every clinical insert takes a `prov` dict and writes a `cn_provenance` row so the
origin (AI model, OCR span, pixel bbox, reviewer, review item) of every value is
recoverable. Nothing here is called outside promote.py.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# NRCeS / ABDM identifier systems
ABHA_SYS = "https://healthid.ndhm.gov.in"
ABHA_ADDR_SYS = "https://healthid.ndhm.gov.in/address"
MRN_SYS = "https://facility.ndhm.gov.in"          # provider-assigned (OIN-scoped)


def _one(sess: Session, sql: str, **p) -> Any:
    return sess.execute(text(sql), p).first()


# --------------------------------------------------------------------------- #
# provenance + audit
# --------------------------------------------------------------------------- #
def add_provenance(sess: Session, *, target_table: str, target_id: str,
                   patient_id: str | None, prov: dict[str, Any]) -> None:
    sess.execute(text("""
        INSERT INTO cn_provenance
          (target_table, target_id, patient_id, activity, agent_type, agent_id,
           occurred_at, source_document_id, ocr_span, bbox, model_id, model_version,
           prompt_version, review_item_id, signature)
        VALUES
          (:tt, :tid, :pid, :act, :atype, :aid, :occ, :sdoc, :span, :bbox, :mid,
           :mver, :pver, :rid, :sig)
    """), {
        "tt": target_table, "tid": target_id, "pid": patient_id,
        "act": prov.get("activity", "promote"),
        "atype": prov.get("agent_type", "clinician"),
        "aid": prov.get("agent_id"),
        "occ": prov.get("occurred_at"),
        "sdoc": prov.get("source_document_id"),
        "span": prov.get("ocr_span"),
        "bbox": prov.get("bbox"),
        "mid": prov.get("model_id"), "mver": prov.get("model_version"),
        "pver": prov.get("prompt_version"), "rid": prov.get("review_item_id"),
        "sig": prov.get("signature"),
    })


def audit(sess: Session, *, actor: str, role: str, patient_id: str | None,
          resource_table: str, resource_id: str | None, action: str,
          reason: str | None = None, source_system: str = "reviewer") -> None:
    sess.execute(text("""
        INSERT INTO cn_audit_event
          (actor_user, actor_role, patient_id, resource_table, resource_id,
           action, source_system, reason)
        VALUES (:a, :r, :p, :rt, :rid, :act, :ss, :reason)
    """), {"a": actor, "r": role, "p": patient_id, "rt": resource_table,
           "rid": resource_id, "act": action, "ss": source_system, "reason": reason})


# --------------------------------------------------------------------------- #
# patient master + identifiers
# --------------------------------------------------------------------------- #
def ensure_patient(sess: Session, *, mpi_id: str | None, name_full: str | None,
                   given: str | None, family: str | None, gender: str | None,
                   birth_date, birth_precision: str | None,
                   prov: dict[str, Any]) -> str:
    """Find-or-create cn_patient by mpi_id (preferred) else by name+dob."""
    row = None
    if mpi_id:
        row = _one(sess, "SELECT id FROM cn_patient WHERE mpi_id = :m", m=mpi_id)
    if not row and name_full and birth_date:
        row = _one(sess, """SELECT id FROM cn_patient
                            WHERE lower(name_full) = lower(:n) AND date_of_birth = :d
                            LIMIT 1""", n=name_full, d=birth_date)
    if row:
        pid = str(row[0])
        sess.execute(text("UPDATE cn_patient SET updated_at = now() WHERE id = :i"), {"i": pid})
        return pid
    ins = _one(sess, """
        INSERT INTO cn_patient
          (mpi_id, name_full, name_given, name_family, gender, date_of_birth,
           birth_date_precision, status)
        VALUES (:m, :nf, :g, :f, :sex, :d, :bp, 'active')
        RETURNING id
    """, m=mpi_id, nf=name_full, g=given, f=family, sex=gender, d=birth_date, bp=birth_precision)
    pid = str(ins[0])
    add_provenance(sess, target_table="cn_patient", target_id=pid, patient_id=pid, prov=prov)
    return pid


def add_identifier(sess: Session, patient_id: str, system: str, value: str,
                   *, use: str = "official", assigner: str | None = None) -> None:
    if not value:
        return
    sess.execute(text("""
        INSERT INTO cn_patient_identifier (patient_id, system, value, use, assigner)
        VALUES (:p, :s, :v, :u, :a)
        ON CONFLICT (patient_id, system, value) DO NOTHING
    """), {"p": patient_id, "s": system, "v": value, "u": use, "a": assigner})


def add_contact(sess: Session, patient_id: str, kind: str, value: str) -> None:
    if not value:
        return
    sess.execute(text("""INSERT INTO cn_patient_contact (patient_id, kind, value, preferred)
                         VALUES (:p, :k, :v, true)"""),
                 {"p": patient_id, "k": kind, "v": value})


def add_address(sess: Session, patient_id: str, line_text: str | None) -> None:
    if not line_text:
        return
    sess.execute(text("""INSERT INTO cn_patient_address (patient_id, line)
                         VALUES (:p, :l)"""), {"p": patient_id, "l": [line_text]})


# --------------------------------------------------------------------------- #
# practitioner / organization
# --------------------------------------------------------------------------- #
def ensure_practitioner(sess: Session, name_full: str | None) -> str | None:
    if not name_full:
        return None
    row = _one(sess, "SELECT id FROM cn_practitioner WHERE lower(name_full) = lower(:n) LIMIT 1",
               n=name_full)
    if row:
        return str(row[0])
    ins = _one(sess, """INSERT INTO cn_practitioner (name_full) VALUES (:n) RETURNING id""",
               n=name_full)
    return str(ins[0])


def ensure_organization(sess: Session, name: str) -> str:
    row = _one(sess, "SELECT id FROM cn_organization WHERE lower(name) = lower(:n) LIMIT 1", n=name)
    if row:
        return str(row[0])
    ins = _one(sess, "INSERT INTO cn_organization (name, type) VALUES (:n, 'prov') RETURNING id", n=name)
    return str(ins[0])


# --------------------------------------------------------------------------- #
# encounter
# --------------------------------------------------------------------------- #
def create_encounter(sess: Session, *, patient_id: str, klass: str, period_start,
                     specialty: str | None, department: str | None,
                     attending: str | None, organization_id: str | None,
                     reason: str | None, derived_from: list[str],
                     prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_encounter
          (patient_id, klass, status, specialty, department, attending_practitioner,
           organization_id, reason_text, period_start, derived_from_documents)
        VALUES (:p, :k, 'finished', :sp, :dp, :att, :org, :rsn, :ps, :df)
        RETURNING id
    """, p=patient_id, k=klass, sp=specialty, dp=department, att=attending,
        org=organization_id, rsn=reason, ps=period_start,
        df=[UUID(d) for d in derived_from] if derived_from else [])
    eid = str(ins[0])
    add_provenance(sess, target_table="cn_encounter", target_id=eid, patient_id=patient_id, prov=prov)
    return eid


# --------------------------------------------------------------------------- #
# clinical resources
# --------------------------------------------------------------------------- #
def insert_condition(sess: Session, *, patient_id: str, encounter_id: str,
                     category: str, display: str, code_system: str | None,
                     code: str | None, clinical_status: str | None,
                     verification: str | None, onset, prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_condition
          (patient_id, encounter_id, category, code_system, code, display,
           clinical_status, verification_status, onset_date, recorded_date)
        VALUES (:p, :e, :cat, :cs, :c, :d, :cls, :vs, :on, CURRENT_DATE)
        RETURNING id
    """, p=patient_id, e=encounter_id, cat=category, cs=code_system, c=code, d=display,
        cls=clinical_status or "active", vs=verification or "provisional", on=onset)
    cid = str(ins[0])
    add_provenance(sess, target_table="cn_condition", target_id=cid, patient_id=patient_id, prov=prov)
    return cid


def insert_observation(sess: Session, *, patient_id: str, encounter_id: str,
                       category: str, display: str, code_system: str | None,
                       code: str | None, value_num, value_unit, value_string,
                       abnormal_flag, effective, prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_observation
          (patient_id, encounter_id, category, code_system, code, display,
           value_num, value_unit_ucum, value_string, interpretation, effective_time,
           issued_time)
        VALUES (:p, :e, :cat, :cs, :c, :d, :vn, :vu, :vs, :flag, :eff, now())
        RETURNING id
    """, p=patient_id, e=encounter_id, cat=category, cs=code_system, c=code, d=display,
        vn=value_num, vu=value_unit, vs=value_string, flag=abnormal_flag, eff=effective)
    oid = str(ins[0])
    add_provenance(sess, target_table="cn_observation", target_id=oid, patient_id=patient_id, prov=prov)
    return oid


def insert_medication_order(sess: Session, *, patient_id: str, encounter_id: str,
                            drug_text: str, dose_num, dose_unit, route,
                            frequency_code, frequency_per_day, timing_text,
                            duration_days, instructions, prescriber_id,
                            strength_num, strength_unit, form, prov: dict[str, Any]) -> str:
    med_id = None
    if strength_num or form:
        m = _one(sess, """INSERT INTO cn_medication
                            (brand_name, strength_num, strength_unit, dosage_form, route_default)
                          VALUES (:b, :sn, :su, :f, :r) RETURNING id""",
                 b=drug_text, sn=strength_num, su=strength_unit, f=form, r=route)
        med_id = str(m[0])
    ins = _one(sess, """
        INSERT INTO cn_medication_order
          (patient_id, encounter_id, medication_id, drug_text, status, intent,
           dose_num, dose_unit_ucum, route, frequency_code, frequency_per_day,
           timing_text, duration_days, instructions, prescriber_id)
        VALUES (:p, :e, :mid, :dt, 'active', 'order', :dn, :du, :rt, :fc, :fpd,
                :tt, :dd, :ins, :pr)
        RETURNING id
    """, p=patient_id, e=encounter_id, mid=med_id, dt=drug_text, dn=dose_num, du=dose_unit,
        rt=route, fc=frequency_code, fpd=frequency_per_day, tt=timing_text, dd=duration_days,
        ins=instructions, pr=prescriber_id)
    mid = str(ins[0])
    add_provenance(sess, target_table="cn_medication_order", target_id=mid, patient_id=patient_id, prov=prov)
    return mid


def insert_lab_result(sess: Session, *, patient_id: str, encounter_id: str,
                      test_name: str, code_system, code, value_num, value_unit,
                      value_string, ref_low, ref_high, ref_text, abnormal_flag,
                      prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_lab_result
          (patient_id, encounter_id, test_code_system, test_code, test_name,
           value_num, value_unit_ucum, value_string, ref_range_low, ref_range_high,
           ref_range_text, abnormal_flag, performed_time)
        VALUES (:p, :e, :cs, :c, :tn, :vn, :vu, :vs, :rl, :rh, :rt, :flag, now())
        RETURNING id
    """, p=patient_id, e=encounter_id, cs=code_system, c=code, tn=test_name, vn=value_num,
        vu=value_unit, vs=value_string, rl=ref_low, rh=ref_high, rt=ref_text, flag=abnormal_flag)
    lid = str(ins[0])
    add_provenance(sess, target_table="cn_lab_result", target_id=lid, patient_id=patient_id, prov=prov)
    return lid


def insert_diagnostic_report(sess: Session, *, patient_id: str, encounter_id: str,
                             category: str, display: str, code_system, code,
                             conclusion, result_obs: list[str], effective,
                             prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_diagnostic_report
          (patient_id, encounter_id, category, code_system, code, display, status,
           effective_time, issued_time, conclusion, result_observations)
        VALUES (:p, :e, :cat, :cs, :c, :d, 'final', :eff, now(), :concl, :ro)
        RETURNING id
    """, p=patient_id, e=encounter_id, cat=category, cs=code_system, c=code, d=display,
        eff=effective, concl=conclusion, ro=[UUID(x) for x in result_obs] if result_obs else [])
    did = str(ins[0])
    add_provenance(sess, target_table="cn_diagnostic_report", target_id=did, patient_id=patient_id, prov=prov)
    return did


def insert_procedure(sess: Session, *, patient_id: str, encounter_id: str,
                     display: str, code_system, code, outcome, body_site,
                     performed, prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_procedure
          (patient_id, encounter_id, code_system, code, display, status,
           performed_time, body_site, outcome)
        VALUES (:p, :e, :cs, :c, :d, 'completed', :pt, :bs, :oc)
        RETURNING id
    """, p=patient_id, e=encounter_id, cs=code_system, c=code, d=display, pt=performed,
        bs=body_site, oc=outcome)
    pid = str(ins[0])
    add_provenance(sess, target_table="cn_procedure", target_id=pid, patient_id=patient_id, prov=prov)
    return pid


def insert_allergy(sess: Session, *, patient_id: str, encounter_id: str,
                   substance_display: str, substance_system, substance_code,
                   category, criticality, prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_allergy
          (patient_id, encounter_id, substance_system, substance_code,
           substance_display, category, criticality, clinical_status,
           verification_status)
        VALUES (:p, :e, :ss, :sc, :sd, :cat, :crit, 'active', 'unconfirmed')
        RETURNING id
    """, p=patient_id, e=encounter_id, ss=substance_system, sc=substance_code,
        sd=substance_display, cat=category, crit=criticality)
    aid = str(ins[0])
    add_provenance(sess, target_table="cn_allergy", target_id=aid, patient_id=patient_id, prov=prov)
    return aid


def insert_document(sess: Session, *, patient_id: str, encounter_id: str,
                    document_type: str, title: str, mime_type: str | None,
                    storage_uri: str | None, sha256: str | None, ocr_text: str | None,
                    source_document_id: str | None, prov: dict[str, Any]) -> str:
    ins = _one(sess, """
        INSERT INTO cn_document
          (patient_id, encounter_id, document_type, title, mime_type, storage_uri,
           sha256, ocr_text, source, source_document_id)
        VALUES (:p, :e, :dt, :t, :mt, :uri, :sha, :ocr, 'SCAN', :sdoc)
        RETURNING id
    """, p=patient_id, e=encounter_id, dt=document_type, t=title, mt=mime_type, uri=storage_uri,
        sha=sha256, ocr=ocr_text, sdoc=source_document_id)
    did = str(ins[0])
    add_provenance(sess, target_table="cn_document", target_id=did, patient_id=patient_id, prov=prov)
    return did
