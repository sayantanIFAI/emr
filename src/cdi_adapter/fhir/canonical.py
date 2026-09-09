"""Build an ABDM FHIR R4 `Bundle(type=document)` from CANONICAL (cn_*) rows.

This is the interoperability layer: it reads Layer-1 tables and emits a NRCeS
profiled document. It never reads `clinical_fact`. One bundle per approved
`rv_item` (i.e. per source document / encounter).
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .resources import (ARTIFACT, FALLBACK_ARTIFACT, LOINC, NRCES, SCT, codeable,
                        dt_iso, new_urn, now_iso)

IG_PACKAGE = "nrces.fhir.r4.ndhm#7.0.0"

# INPS dedicated vital-sign profiles (v7) keyed by a keyword in the display text
_VITAL_PROFILE = [
    ("systolic", "ObservationBP"), ("diastolic", "ObservationBP"), ("blood pressure", "ObservationBP"),
    ("heart rate", "ObservationHeartRate"), ("pulse", "ObservationHeartRate"),
    ("oxygen", "ObservationOxygenSat"), ("spo2", "ObservationOxygenSat"),
    ("respirat", "ObservationRespRate"),
    ("temp", "ObservationBodyTemp"),
    ("weight", "ObservationBodyWeight"), ("height", "ObservationBodyHeight"),
    ("bmi", "ObservationBMI"), ("head circ", "ObservationHeadCircum"),
]
_VITAL_LOINC = {
    "ObservationBP": ("85354-9", "Blood pressure panel"),
    "ObservationHeartRate": ("8867-4", "Heart rate"),
    "ObservationOxygenSat": ("2708-6", "Oxygen saturation"),
    "ObservationRespRate": ("9279-1", "Respiratory rate"),
    "ObservationBodyTemp": ("8310-5", "Body temperature"),
    "ObservationBodyWeight": ("29463-7", "Body weight"),
    "ObservationBodyHeight": ("8302-2", "Body height"),
    "ObservationBMI": ("39156-5", "Body mass index"),
    "ObservationHeadCircum": ("9843-4", "Head circumference"),
}

# which Composition sections each artifact declares (title, LOINC section code)
_SECTIONS: dict[str, list[tuple[str, str, str]]] = {
    "OPConsultRecord": [
        ("Chief Complaints", "10154-3", "condition"),
        ("Physical Examination", "425044008", "vital"),
        ("Allergies", "48765-2", "allergy"),
        ("Medical History", "11348-0", "condition"),
        ("Investigation Advice", "721963009", "servicerequest"),
        ("Medications", "10160-0", "medication"),
        ("Procedure", "29554-3", "procedure"),
        ("Follow Up", "390906007", "followup"),
    ],
    "PrescriptionRecord": [
        ("Medications", "10160-0", "medication"),
        ("Follow Up", "390906007", "followup"),
    ],
    "DiagnosticReportRecord": [
        ("Diagnostic Report", "721981007", "report"),
    ],
    "DischargeSummaryRecord": [
        ("Chief Complaints", "10154-3", "condition"),
        ("Medical History", "11348-0", "condition"),
        ("Physical Examination", "425044008", "vital"),
        ("Procedure", "29554-3", "procedure"),
        ("Medications", "10160-0", "medication"),
        ("Follow Up", "390906007", "followup"),
    ],
    "WellnessRecord": [
        ("Vital Signs", "85353-1", "vital"),
    ],
    "HealthDocumentRecord": [
        ("Document Reference", "51899-3", "document"),
    ],
    "ImmunizationRecord": [
        ("Immunizations", "11369-6", "immunization"),
    ],
}


def _m(profile: str) -> dict[str, Any]:
    return {"profile": [NRCES + profile]}


def _q(row: dict[str, Any], num_key: str, unit_key: str) -> dict[str, Any] | None:
    n = row.get(num_key)
    if n is None:
        return None
    return {"value": float(n), "unit": row.get(unit_key) or "1",
            "system": "http://unitsofmeasure.org", "code": row.get(unit_key) or "1"}


def build_bundle(sess: Session, rv_item_id: str) -> dict[str, Any]:
    item = sess.execute(text("SELECT * FROM rv_item WHERE id = :i"),
                        {"i": rv_item_id}).mappings().first()
    if not item or not item["canonical_patient_id"]:
        raise ValueError("review item not promoted")
    pid = str(item["canonical_patient_id"])
    eid = str(item["encounter_id"]) if item["encounter_id"] else None
    doc_type = item["doc_type"] or "opd_note"
    profile, type_coding, title = ARTIFACT.get(doc_type, FALLBACK_ARTIFACT)

    pat = sess.execute(text("SELECT * FROM cn_patient WHERE id = :i"), {"i": pid}).mappings().first()
    idents = sess.execute(text("SELECT * FROM cn_patient_identifier WHERE patient_id = :i"),
                          {"i": pid}).mappings().all()
    enc = (sess.execute(text("SELECT * FROM cn_encounter WHERE id = :i"), {"i": eid}).mappings().first()
           if eid else None)
    conds = sess.execute(text("SELECT * FROM cn_condition WHERE encounter_id = :e"),
                         {"e": eid}).mappings().all() if eid else []
    obs = sess.execute(text("SELECT * FROM cn_observation WHERE encounter_id = :e"),
                       {"e": eid}).mappings().all() if eid else []
    meds = sess.execute(text("SELECT * FROM cn_medication_order WHERE encounter_id = :e"),
                        {"e": eid}).mappings().all() if eid else []
    procs = sess.execute(text("SELECT * FROM cn_procedure WHERE encounter_id = :e"),
                         {"e": eid}).mappings().all() if eid else []
    allgs = sess.execute(text("SELECT * FROM cn_allergy WHERE encounter_id = :e"),
                         {"e": eid}).mappings().all() if eid else []
    reports = sess.execute(text("SELECT * FROM cn_diagnostic_report WHERE encounter_id = :e"),
                           {"e": eid}).mappings().all() if eid else []

    entries: list[dict[str, Any]] = []
    urn_of: dict[str, str] = {}

    def add(resource: dict[str, Any]) -> str:
        u = new_urn()
        resource["id"] = u.split(":")[-1]
        entries.append({"fullUrl": u, "resource": resource})
        return u

    # ---- Patient (ABHA + MRN as distinct identifiers) ------------------
    p_ident = []
    for i in idents:
        if i["system"] in ("https://healthid.ndhm.gov.in", "https://healthid.ndhm.gov.in/address"):
            p_ident.append({"type": {"coding": [{"system": "https://ndhm.gov.in/sms/identifier-type",
                                                 "code": "ABHA", "display": "Ayushman Bharat Health Account"}]},
                            "system": i["system"], "value": i["value"]})
        else:
            p_ident.append({"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                                 "code": "MR", "display": "Medical record number"}]},
                            "system": i["system"], "value": i["value"], "assigner": {"display": i["assigner"] or "Provider"}})
    if not p_ident:
        p_ident = [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                    "system": "https://facility.ndhm.gov.in", "value": pat["mpi_id"] or "UNKNOWN"}]
    gender_map = {"M": "male", "F": "female", "O": "other"}
    pat_res = {"resourceType": "Patient", "meta": _m("Patient"), "identifier": p_ident,
               "name": [{"text": pat["name_full"] or "Unknown",
                         "family": pat.get("name_family"),
                         "given": [pat["name_given"]] if pat.get("name_given") else None}],
               "gender": gender_map.get((pat.get("gender") or "").upper(), "unknown")}
    if pat.get("date_of_birth"):
        pat_res["birthDate"] = str(pat["date_of_birth"])[:10]
    _prune(pat_res)
    u_pat = add(pat_res)

    # ---- Organization + Practitioner (author) -------------------------
    u_org = add({"resourceType": "Organization", "meta": _m("Organization"),
                 "identifier": [{"system": "https://facility.ndhm.gov.in", "value": "IN-UNKNOWN"}],
                 "name": "CareFlow Polyclinic"})
    u_pract = add({"resourceType": "Practitioner", "meta": _m("Practitioner"),
                   "identifier": [{"system": "https://doctor.ndhm.gov.in", "value": "HPR-UNKNOWN"}],
                   "name": [{"text": "Attending Physician"}]})

    # ---- Encounter --------------------------------------------------
    u_enc = None
    if enc:
        klass = {"IPD": ("IMP", "inpatient encounter"), "ICU": ("IMP", "inpatient encounter"),
                 "ER": ("EMER", "emergency"), "TELEMEDICINE": ("VR", "virtual"),
                 "DIAGNOSTIC": ("AMB", "ambulatory")}.get(enc["klass"], ("AMB", "ambulatory"))
        u_enc = add({"resourceType": "Encounter", "meta": _m("Encounter"),
                     "status": enc["status"] or "finished",
                     "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                               "code": klass[0], "display": klass[1]},
                     "subject": {"reference": u_pat},
                     "period": {"start": dt_iso(enc.get("period_start"))}})

    def _clin_status(v: str | None) -> dict[str, Any]:
        return {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": v or "active"}]}

    cond_urns, vital_urns, med_urns, proc_urns, allergy_urns, report_urns, sr_urns = ([] for _ in range(7))

    for c in conds:
        u = add({"resourceType": "Condition", "meta": _m("Condition"),
                 "clinicalStatus": _clin_status(c.get("clinical_status")),
                 "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                                    "code": c.get("verification_status") or "provisional"}]},
                 "code": codeable(c.get("code_system"), c.get("code"), c.get("display"), c.get("display")),
                 "subject": {"reference": u_pat},
                 "encounter": {"reference": u_enc} if u_enc else None})
        _prune(entries[-1]["resource"])
        cond_urns.append(u)

    for o in obs:
        prof = "Observation"
        loinc_code = o.get("code"); loinc_disp = o.get("display")
        if o["category"] == "vital-sign":
            for kw, pf in _VITAL_PROFILE:
                if kw in (o.get("display") or "").lower():
                    prof = pf
                    if not loinc_code and pf in _VITAL_LOINC:
                        loinc_code, loinc_disp = _VITAL_LOINC[pf]
                    break
        res = {"resourceType": "Observation", "meta": _m(prof),
               "status": "final",
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": {"vital-sign": "vital-signs", "laboratory": "laboratory",
                                                  "imaging": "imaging", "exam": "exam"}.get(o["category"], "exam")}]}],
               "code": codeable(o.get("code_system") or LOINC, loinc_code, loinc_disp, o.get("display")),
               "subject": {"reference": u_pat},
               "encounter": {"reference": u_enc} if u_enc else None,
               "effectiveDateTime": dt_iso(o.get("effective_time")) or now_iso()}
        q = _q(o, "value_num", "value_unit_ucum")
        if q:
            res["valueQuantity"] = q
        elif o.get("value_string"):
            res["valueString"] = o["value_string"]
        else:
            res["dataAbsentReason"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                                                   "code": "unknown"}]}
        _prune(res)
        u = add(res)
        (vital_urns if o["category"] == "vital-sign" else report_urns).append(u)

    for m in meds:
        dosage = {"text": " ".join(x for x in [m.get("timing_text"), m.get("instructions")] if x) or "As directed"}
        if m.get("frequency_per_day"):
            dosage["timing"] = {"repeat": {"frequency": int(m["frequency_per_day"]), "period": 1, "periodUnit": "d"}}
        if m.get("dose_num"):
            dosage["doseAndRate"] = [{"doseQuantity": _q(m, "dose_num", "dose_unit_ucum")}]
        u = add({"resourceType": "MedicationRequest", "meta": _m("MedicationRequest"),
                 "status": m.get("status") or "active", "intent": m.get("intent") or "order",
                 "medicationCodeableConcept": codeable(None, None, None, m["drug_text"]),
                 "subject": {"reference": u_pat},
                 "encounter": {"reference": u_enc} if u_enc else None,
                 "authoredOn": now_iso(),
                 "requester": {"reference": u_pract},
                 "dosageInstruction": [dosage]})
        _prune(entries[-1]["resource"])
        med_urns.append(u)

    for pr in procs:
        u = add({"resourceType": "Procedure", "meta": _m("Procedure"),
                 "status": pr.get("status") or "completed",
                 "code": codeable(pr.get("code_system"), pr.get("code"), pr.get("display"), pr.get("display")),
                 "subject": {"reference": u_pat},
                 "encounter": {"reference": u_enc} if u_enc else None,
                 "performedDateTime": dt_iso(pr.get("performed_time")) or now_iso()})
        _prune(entries[-1]["resource"])
        proc_urns.append(u)

    for a in allgs:
        u = add({"resourceType": "AllergyIntolerance", "meta": _m("AllergyIntolerance"),
                 "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                                "code": a.get("clinical_status") or "active"}]},
                 "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                                                    "code": a.get("verification_status") or "unconfirmed"}]},
                 "code": codeable(a.get("substance_system"), a.get("substance_code"),
                                  a.get("substance_display"), a.get("substance_display")),
                 "patient": {"reference": u_pat},
                 "criticality": a.get("criticality") or "unable-to-assess"})
        _prune(entries[-1]["resource"])
        allergy_urns.append(u)

    for r in reports:
        u = add({"resourceType": "DiagnosticReport", "meta": _m("DiagnosticReport"),
                 "status": r.get("status") or "final",
                 "code": codeable(r.get("code_system") or SCT, r.get("code"), r.get("display"), r.get("display")),
                 "subject": {"reference": u_pat},
                 "encounter": {"reference": u_enc} if u_enc else None,
                 "effectiveDateTime": dt_iso(r.get("effective_time")) or now_iso(),
                 "issued": now_iso(),
                 "conclusion": r.get("conclusion"),
                 "result": [{"reference": x} for x in report_urns] or None})
        _prune(entries[-1]["resource"])
        report_urns_top = u
        report_urns.append(u)

    # ---- Composition -------------------------------------------------
    kind_urns = {
        "condition": cond_urns, "vital": vital_urns, "medication": med_urns,
        "procedure": proc_urns, "allergy": allergy_urns, "report": report_urns,
        "servicerequest": sr_urns, "document": [], "followup": [], "immunization": [],
    }
    sections = []
    for sec_title, sec_code, kind in _SECTIONS.get(profile, _SECTIONS["HealthDocumentRecord"]):
        urns = kind_urns.get(kind, [])
        if not urns:
            continue
        sections.append({
            "title": sec_title,
            "code": codeable(LOINC, sec_code, sec_title, sec_title),
            "entry": [{"reference": u} for u in urns],
        })
    comp = {"resourceType": "Composition", "meta": _m(profile),
            "status": "final",
            "type": codeable(type_coding[0], type_coding[1], type_coding[2], title),
            "subject": {"reference": u_pat},
            "encounter": {"reference": u_enc} if u_enc else None,
            "date": now_iso(),
            "author": [{"reference": u_pract}, {"reference": u_org}],
            "title": title,
            "section": sections or [{"title": title, "code": codeable(LOINC, "51899-3", title),
                                     "entry": [{"reference": u_pat}]}]}
    _prune(comp)
    u_comp = new_urn()
    comp["id"] = u_comp.split(":")[-1]
    entries.insert(0, {"fullUrl": u_comp, "resource": comp})

    bundle = {
        "resourceType": "Bundle",
        "meta": {"profile": [NRCES + "DocumentBundle"]},
        "identifier": {"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{uuid4()}"},
        "type": "document",
        "timestamp": now_iso(),
        "entry": entries,
    }
    return {"bundle": bundle, "artifact": profile, "ig_package": IG_PACKAGE}


def _prune(o: Any) -> Any:
    """Drop None values / empty containers recursively (in place for dicts/lists)."""
    if isinstance(o, dict):
        for k in list(o.keys()):
            v = _prune(o[k])
            if v is None or v == [] or v == {}:
                del o[k]
        return o
    if isinstance(o, list):
        return [x for x in (_prune(i) for i in o) if x is not None and x != {} and x != []]
    return o
