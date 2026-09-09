"""FHIR R4 resource builders (plain dicts) with ABDM (NRCeS) profile assertions.

References inside a document Bundle use urn:uuid full-urls, per the ABDM IG.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

SCT = "http://snomed.info/sct"
LOINC = "http://loinc.org"
NRCES = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"
ABHA_SYS = "https://healthid.ndhm.gov.in"
ABHA_ADDR_SYS = "https://healthid.ndhm.gov.in/address"

# doc_type -> (ABDM artifact profile, Composition.type coding, human title)
ARTIFACT = {
    "prescription":       ("PrescriptionRecord",    (SCT, "440545006", "Prescription record"),        "Prescription record"),
    "opd_note":           ("OPConsultRecord",       (SCT, "371530004", "Consultation report"),        "OP consultation record"),
    "referral":           ("OPConsultRecord",       (SCT, "371530004", "Consultation report"),        "OP consultation record"),
    "lab_report":         ("DiagnosticReportRecord",(SCT, "721981007", "Diagnostic studies report"),  "Diagnostic report - lab"),
    "radiology_report":   ("DiagnosticReportRecord",(SCT, "721981007", "Diagnostic studies report"),  "Diagnostic report - imaging"),
    "discharge_summary":  ("DischargeSummaryRecord",(SCT, "373942005", "Discharge summary"),           "Discharge summary record"),
    "operative_note":     ("DischargeSummaryRecord",(SCT, "373942005", "Discharge summary"),           "Discharge summary record"),
    "vitals_sheet":       ("WellnessRecord",        (SCT, "886731000000109", "Wellness record"),       "Wellness record"),
    "immunization_card":  ("ImmunizationRecord",    (SCT, "41000179103", "Immunization record"),       "Immunization record"),
}
FALLBACK_ARTIFACT = ("HealthDocumentRecord", (SCT, "419891008", "Record artifact"), "Health document record")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def dt_iso(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return str(d)


def new_urn() -> str:
    return f"urn:uuid:{_uuid.uuid4()}"


def codeable(system: str | None, code: str | None, display: str | None,
             text: str | None = None) -> dict[str, Any]:
    cc: dict[str, Any] = {}
    if system and code:
        cc["coding"] = [{"system": system, "code": code, "display": display or text or code}]
    cc["text"] = text or display or code or "unspecified"
    return cc


def _meta(profile: str) -> dict[str, Any]:
    return {"profile": [NRCES + profile]}


# --------------------------------------------------------------------------- #
def patient(row: dict[str, Any], urn: str) -> dict[str, Any]:
    name = (row.get("name_full")
            or " ".join(x for x in [row.get("name_given"), row.get("name_family")] if x)
            or "Unknown")
    r: dict[str, Any] = {
        "resourceType": "Patient",
        "id": urn.split(":")[-1],
        "meta": _meta("Patient"),
        "name": [{"text": name}],
    }
    ids = []
    if row.get("mpi_id"):
        ids.append({"system": "https://careflow.clinic/mpi", "value": row["mpi_id"],
                    "type": codeable(None, None, "CareFlow patient id"), "use": "usual"})
    if row.get("abha_number"):
        ids.append({"system": ABHA_SYS, "value": row["abha_number"],
                    "type": codeable(None, None, "ABHA Number")})
    if row.get("abha_address"):
        ids.append({"system": ABHA_ADDR_SYS, "value": row["abha_address"]})
    if row.get("legacy_mrn"):
        ids.append({"system": "urn:cdi:legacy-mrn", "value": row["legacy_mrn"]})
    if ids:
        r["identifier"] = ids
    if row.get("gender"):
        r["gender"] = {"M": "male", "F": "female", "O": "other"}.get(row["gender"], "unknown")
    if row.get("birth_date"):
        r["birthDate"] = str(row["birth_date"])[:10]
    return r


def organization(urn: str, name: str = "City Care Hospital") -> dict[str, Any]:
    return {"resourceType": "Organization", "id": urn.split(":")[-1],
            "meta": _meta("Organization"), "name": name,
            "identifier": [{"system": "https://facility.ndhm.gov.in", "value": "IN-UNKNOWN"}]}


def practitioner(urn: str, name: str | None) -> dict[str, Any]:
    return {"resourceType": "Practitioner", "id": urn.split(":")[-1],
            "meta": _meta("Practitioner"),
            "name": [{"text": name or "Unknown Practitioner"}]}


def encounter(row: dict[str, Any], urn: str, patient_ref: str) -> dict[str, Any]:
    cls = row.get("class") or "AMB"
    disp = {"AMB": "ambulatory", "IMP": "inpatient encounter", "EMER": "emergency"}.get(cls, "ambulatory")
    r = {
        "resourceType": "Encounter",
        "id": urn.split(":")[-1],
        "meta": _meta("Encounter"),
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                  "code": cls, "display": disp},
        "subject": {"reference": patient_ref},
    }
    if row.get("period_start"):
        r["period"] = {"start": dt_iso(row["period_start"])}
        if row.get("period_end"):
            r["period"]["end"] = dt_iso(row["period_end"])
    return r


def _base_clin(rt: str, urn: str, profile: str, patient_ref: str, enc_ref: str | None) -> dict[str, Any]:
    r = {"resourceType": rt, "id": urn.split(":")[-1], "meta": _meta(profile),
         "subject": {"reference": patient_ref}}
    if enc_ref:
        r["encounter"] = {"reference": enc_ref}
    return r


def condition(f: dict[str, Any], urn: str, patient_ref: str, enc_ref: str | None) -> dict[str, Any]:
    r = _base_clin("Condition", urn, "Condition", patient_ref, enc_ref)
    r["clinicalStatus"] = codeable(
        "http://terminology.hl7.org/CodeSystem/condition-clinical",
        f.get("clinical_status") or "active", f.get("clinical_status") or "active")
    r["verificationStatus"] = codeable(
        "http://terminology.hl7.org/CodeSystem/condition-ver-status",
        "confirmed" if (f.get("verification") in (None, "confirmed")) else "unconfirmed",
        "Confirmed")
    r["code"] = codeable(f.get("code_system"), f.get("code"), f.get("code_display"),
                         f.get("local_text"))
    if f.get("onset"):
        r["onsetDateTime"] = dt_iso(f["onset"])
    return r


def observation(f: dict[str, Any], urn: str, patient_ref: str, enc_ref: str | None,
                category: str) -> dict[str, Any]:
    r = _base_clin("Observation", urn, "Observation", patient_ref, enc_ref)
    r["status"] = "final"
    cat_disp = {"laboratory": "Laboratory", "vital-signs": "Vital Signs", "exam": "Exam"}[category]
    r["category"] = [codeable("http://terminology.hl7.org/CodeSystem/observation-category",
                              category, cat_disp)]
    r["code"] = codeable(f.get("code_system"), f.get("code"), f.get("code_display"),
                         f.get("local_text"))
    if f.get("value_num") is not None:
        r["valueQuantity"] = {"value": float(f["value_num"]),
                              "unit": f.get("value_unit_ucum") or "",
                              "system": "http://unitsofmeasure.org",
                              "code": f.get("value_unit_ucum") or ""}
    elif f.get("value_text"):
        r["valueString"] = f["value_text"]
    if f.get("effective_time"):
        r["effectiveDateTime"] = dt_iso(f["effective_time"])
    if f.get("abnormal_flag"):
        r["interpretation"] = [codeable(
            "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            f["abnormal_flag"], f["abnormal_flag"])]
    if f.get("ref_range_low") is not None or f.get("ref_range_high") is not None or f.get("ref_range_text"):
        rr: dict[str, Any] = {}
        if f.get("ref_range_low") is not None:
            rr["low"] = {"value": float(f["ref_range_low"]), "unit": f.get("value_unit_ucum") or ""}
        if f.get("ref_range_high") is not None:
            rr["high"] = {"value": float(f["ref_range_high"]), "unit": f.get("value_unit_ucum") or ""}
        if f.get("ref_range_text"):
            rr["text"] = f["ref_range_text"]
        r["referenceRange"] = [rr]
    return r


def medication_request(f: dict[str, Any], md: dict[str, Any] | None, urn: str,
                       patient_ref: str, enc_ref: str | None,
                       requester_ref: str | None) -> dict[str, Any]:
    r = _base_clin("MedicationRequest", urn, "MedicationRequest", patient_ref, enc_ref)
    r["status"] = "active" if (f.get("clinical_status") in (None, "active")) else (f.get("clinical_status") or "stopped")
    r["intent"] = "order"
    md = md or {}
    r["medicationCodeableConcept"] = codeable(
        f.get("code_system"), f.get("code"), f.get("code_display"),
        md.get("drug_text") or f.get("local_text"))
    if requester_ref:
        r["requester"] = {"reference": requester_ref}
    dosage: dict[str, Any] = {}
    txt_bits = []
    if md.get("dose_num") is not None:
        dosage["doseAndRate"] = [{"doseQuantity": {
            "value": float(md["dose_num"]), "unit": md.get("dose_unit_ucum") or "",
            "system": "http://unitsofmeasure.org", "code": md.get("dose_unit_ucum") or ""}}]
        txt_bits.append(f"{md['dose_num']} {md.get('dose_unit_ucum') or ''}".strip())
    elif md.get("strength_num") is not None:
        txt_bits.append(f"{md['strength_num']} {md.get('strength_unit') or ''}".strip())
    if md.get("frequency_per_day"):
        dosage["timing"] = {"repeat": {"frequency": int(md["frequency_per_day"]),
                                       "period": 1, "periodUnit": "d"}}
        txt_bits.append(md.get("frequency_code") or f"{int(md['frequency_per_day'])}x/day")
    elif md.get("frequency_code"):
        txt_bits.append(md["frequency_code"])
    if md.get("route"):
        dosage["route"] = codeable(None, None, md["route"])
    if md.get("instructions"):
        txt_bits.append(md["instructions"])
    if txt_bits:
        dosage["text"] = ", ".join(t for t in txt_bits if t)
    if dosage:
        r["dosageInstruction"] = [dosage]
    if md.get("duration_days"):
        r["dispenseRequest"] = {"expectedSupplyDuration": {
            "value": md["duration_days"], "unit": "days",
            "system": "http://unitsofmeasure.org", "code": "d"}}
    return r


def procedure(f: dict[str, Any], urn: str, patient_ref: str, enc_ref: str | None) -> dict[str, Any]:
    r = _base_clin("Procedure", urn, "Procedure", patient_ref, enc_ref)
    r["status"] = "completed"
    r["code"] = codeable(f.get("code_system"), f.get("code"), f.get("code_display"),
                         f.get("local_text"))
    if f.get("effective_time"):
        r["performedDateTime"] = dt_iso(f["effective_time"])
    if f.get("value_text"):
        r["note"] = [{"text": f["value_text"]}]
    return r


def allergy_intolerance(f: dict[str, Any], urn: str, patient_ref: str,
                        enc_ref: str | None) -> dict[str, Any]:
    r = _base_clin("AllergyIntolerance", urn, "AllergyIntolerance", patient_ref, enc_ref)
    r.pop("subject", None)
    r["patient"] = {"reference": patient_ref}
    r["clinicalStatus"] = codeable(
        "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "active", "Active")
    r["verificationStatus"] = codeable(
        "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
        "unconfirmed", "Unconfirmed")
    r["code"] = codeable(f.get("code_system"), f.get("code"), f.get("code_display"),
                         f.get("local_text"))
    if f.get("value_text"):
        r["reaction"] = [{"manifestation": [codeable(None, None, f["value_text"])]}]
    return r


def diagnostic_report(f: dict[str, Any], urn: str, patient_ref: str, enc_ref: str | None,
                      result_refs: list[str], doc_ref_urn: str | None) -> dict[str, Any]:
    r = _base_clin("DiagnosticReport", urn, "DiagnosticReport", patient_ref, enc_ref)
    r["status"] = "final"
    r["code"] = codeable(f.get("code_system"), f.get("code"),
                         f.get("code_display") or f.get("local_text"), f.get("local_text"))
    if f.get("effective_time"):
        r["effectiveDateTime"] = dt_iso(f["effective_time"])
    if result_refs:
        r["result"] = [{"reference": x} for x in result_refs]
    if f.get("value_text"):
        r["conclusion"] = f["value_text"]
    if doc_ref_urn:
        r["presentedForm"] = []  # filled by caller with attachment
    return r


def document_reference(doc_row: dict[str, Any], urn: str, patient_ref: str,
                       b64: str | None, doc_type_display: str) -> dict[str, Any]:
    att: dict[str, Any] = {"contentType": doc_row.get("mime_type") or "application/pdf",
                           "title": doc_row.get("original_filename") or "scanned document",
                           "creation": dt_iso(doc_row.get("captured_at") or doc_row.get("ingested_at"))}
    if b64:
        att["data"] = b64
    else:
        att["url"] = f"/api/documents/{doc_row['id']}/original"
    return {
        "resourceType": "DocumentReference",
        "id": urn.split(":")[-1],
        "meta": _meta("DocumentReference"),
        "status": "current",
        "type": codeable(SCT, "419891008", "Record artifact", doc_type_display),
        "subject": {"reference": patient_ref},
        "content": [{"attachment": att}],
    }


def provenance(target_refs: list[str], urn: str, doc_ref_urn: str, when: str,
               model_stack: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceType": "Provenance",
        "id": urn.split(":")[-1],
        "target": [{"reference": t} for t in target_refs],
        "recorded": when,
        "entity": [{"role": "source", "what": {"reference": doc_ref_urn}}],
        "agent": [{
            "type": codeable("http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                             "assembler", "Assembler"),
            "who": {"display": "CDI-Adapter " + ",".join(f"{k}={v}" for k, v in model_stack.items())},
        }],
    }


def composition(*, urn: str, profile: str, type_coding: tuple[str, str, str], title: str,
                patient_ref: str, author_refs: list[str], enc_ref: str | None,
                sections: list[dict[str, Any]], date: str, custodian_ref: str | None) -> dict[str, Any]:
    r: dict[str, Any] = {
        "resourceType": "Composition",
        "id": urn.split(":")[-1],
        "meta": _meta(profile),
        "status": "final",
        "type": codeable(*type_coding),
        "subject": {"reference": patient_ref},
        "date": date,
        "author": [{"reference": a} for a in author_refs] or [{"display": "CDI-Adapter"}],
        "title": title,
        "section": sections,
    }
    if enc_ref:
        r["encounter"] = {"reference": enc_ref}
    if custodian_ref:
        r["custodian"] = {"reference": custodian_ref}
    return r


def section(title: str, code_text: str, entry_urns: list[str],
            text_div: str | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {"title": title, "code": codeable(None, None, code_text),
                         "entry": [{"reference": u} for u in entry_urns]}
    if text_div:
        s["text"] = {"status": "generated",
                     "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{text_div}</div>'}
    return s


def bundle_document(bundle_urn: str, entries: list[tuple[str, dict[str, Any]]],
                    timestamp: str, identifier_value: str) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "id": bundle_urn.split(":")[-1],
        "meta": {"profile": [NRCES + "DocumentBundle"],
                 "lastUpdated": timestamp},
        "identifier": {"system": "urn:cdi:bundle", "value": identifier_value},
        "type": "document",
        "timestamp": timestamp,
        "entry": [{"fullUrl": u, "resource": r} for u, r in entries],
    }
