from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from .. import repo, storage
from ..config import settings
from ..db import session_scope
from ..logging import get_logger
from . import resources as R

log = get_logger(__name__)

_MODEL_STACK = {"classifier": "qwen2.5-vl-7b", "ocr": "rapidocr", "extractor": "qwen2.5-vl-7b",
                "terminology": "seed-v1", "projector": "deterministic"}
_MAX_INLINE_BYTES = 12 * 1024 * 1024


def _obs_category(f: dict[str, Any]) -> str:
    return {"lab_result": "laboratory", "vital_sign": "vital-signs"}.get(f["fact_type"], "exam")


def project_document(document_id: str | UUID, *, persist: bool = True) -> dict[str, Any]:
    document_id = str(document_id)
    with session_scope() as sess:
        doc = repo.get_document(sess, document_id)
        if not doc:
            raise ValueError(f"document {document_id} not found")
        cls = repo.get_doc_classification(sess, document_id)
        all_facts = repo.list_clinical_facts(sess, document_id=document_id)
        # GOVERNANCE GATE: only facts that passed S6 (or a clinician) are asserted in FHIR.
        _GOVERNED = ("auto_accepted", "clinician_confirmed", "corrected")
        facts = [f for f in all_facts if f.get("review_state") in _GOVERNED]
        held = [f for f in all_facts if f.get("review_state") not in _GOVERNED]
        pages = repo.list_document_pages(sess, document_id)
        run_id = repo.start_pipeline_run(sess, document_id=document_id, stage="project",
                                         model_name="fhir-mapper")
        # patient + encounter from the facts (extract stage set them)
        _anchor = (facts or all_facts or [{}])[0]
        pid = _anchor.get("patient_id")
        eid = _anchor.get("encounter_id")
        if pid:
            prow = sess.execute(text("SELECT * FROM patient_identity WHERE id=:i"),
                                {"i": str(pid)}).mappings().first()
            prow = dict(prow) if prow else {}
        else:
            prow = {"name_given": doc.get("legacy_patient_ref") or "Unknown"}
        erow = {}
        if eid:
            er = sess.execute(text("SELECT * FROM encounter WHERE id=:i"),
                              {"i": str(eid)}).mappings().first()
            erow = dict(er) if er else {}
        med_details = {str(f["id"]): repo.get_medication_detail(sess, f["id"])
                       for f in facts if f["fact_type"] == "medication"}
        original = None
        try:
            key = storage.key_from_uri(doc["object_uri"])
            raw = storage.get_bytes(key)
            if len(raw) <= _MAX_INLINE_BYTES:
                original = base64.b64encode(raw).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            log.warning("docref_inline_skipped", error=str(exc)[:120])

    doc_type = (cls or {}).get("doc_type", "other")
    profile, type_coding, title = R.ARTIFACT.get(doc_type, R.FALLBACK_ARTIFACT)
    when = R.now_iso()

    # ---- urns ----
    u_pat = R.new_urn()
    u_enc = R.new_urn() if erow else None
    u_org = R.new_urn()
    u_prac = R.new_urn()
    u_doc = R.new_urn()
    u_bundle = R.new_urn()
    u_prov = R.new_urn()
    pat_ref, enc_ref = u_pat, u_enc

    entries: list[tuple[str, dict[str, Any]]] = []
    resource_rows: list[dict[str, Any]] = []
    by_kind: dict[str, list[str]] = {}
    fact_urn: dict[str, str] = {}
    diag_result_urns: list[str] = []

    def emit(urn: str, res: dict[str, Any], *, rtype: str, fact_ids: list[str]) -> None:
        entries.append((urn, res))
        resource_rows.append({"rtype": rtype, "fhir_id": res["id"], "urn": urn,
                              "profile": res.get("meta", {}).get("profile", []),
                              "resource": res, "facts": fact_ids})

    # ---- core actors ----
    emit(u_pat, R.patient(prow, u_pat), rtype="Patient", fact_ids=[])
    emit(u_org, R.organization(u_org, (erow.get("facility_ref") or "City Care Hospital")),
         rtype="Organization", fact_ids=[])
    emit(u_prac, R.practitioner(u_prac, erow.get("practitioner_ref")),
         rtype="Practitioner", fact_ids=[])
    if erow:
        emit(u_enc, R.encounter(erow, u_enc, pat_ref), rtype="Encounter", fact_ids=[])

    # ---- clinical facts -> resources ----
    for f in facts:
        fid = str(f["id"])
        u = R.new_urn()
        fact_urn[fid] = u
        ft = f["fact_type"]
        if ft == "condition":
            emit(u, R.condition(f, u, pat_ref, enc_ref), rtype="Condition", fact_ids=[fid])
            by_kind.setdefault("diagnoses", []).append(u)
        elif ft in ("symptom",):
            emit(u, R.observation(f, u, pat_ref, enc_ref, "exam"), rtype="Observation", fact_ids=[fid])
            by_kind.setdefault("complaints", []).append(u)
        elif ft == "finding":
            emit(u, R.observation(f, u, pat_ref, enc_ref, "exam"), rtype="Observation", fact_ids=[fid])
            by_kind.setdefault("investigations", []).append(u)
        elif ft == "lab_result":
            emit(u, R.observation(f, u, pat_ref, enc_ref, "laboratory"), rtype="Observation", fact_ids=[fid])
            by_kind.setdefault("investigations", []).append(u)
            diag_result_urns.append(u)
        elif ft == "vital_sign":
            emit(u, R.observation(f, u, pat_ref, enc_ref, "vital-signs"), rtype="Observation", fact_ids=[fid])
            by_kind.setdefault("vitals", []).append(u)
        elif ft == "medication":
            emit(u, R.medication_request(f, med_details.get(fid), u, pat_ref, enc_ref, u_prac),
                 rtype="MedicationRequest", fact_ids=[fid])
            by_kind.setdefault("medications", []).append(u)
        elif ft == "procedure":
            emit(u, R.procedure(f, u, pat_ref, enc_ref), rtype="Procedure", fact_ids=[fid])
            by_kind.setdefault("procedures", []).append(u)
        elif ft == "allergy":
            emit(u, R.allergy_intolerance(f, u, pat_ref, enc_ref), rtype="AllergyIntolerance", fact_ids=[fid])
            by_kind.setdefault("allergies", []).append(u)
        elif ft == "advice":
            by_kind.setdefault("advice_text", []).append(f.get("value_text") or f.get("local_text") or "")
        elif ft == "diagnostic_report":
            dr = R.diagnostic_report(f, u, pat_ref, enc_ref, diag_result_urns, u_doc)
            emit(u, dr, rtype="DiagnosticReport", fact_ids=[fid])
            by_kind.setdefault("investigations", []).insert(0, u)

    # ---- document reference (the scan) + provenance ----
    emit(u_doc, R.document_reference(doc, u_doc, pat_ref, original, title),
         rtype="DocumentReference", fact_ids=[])
    _clin = {"Condition", "Observation", "MedicationRequest", "Procedure",
             "AllergyIntolerance", "DiagnosticReport"}
    emitted_clin_urns = [rr["urn"] for rr in resource_rows if rr["rtype"] in _clin]
    emit(u_prov, R.provenance(emitted_clin_urns + [u_doc], u_prov, u_doc, when, _MODEL_STACK),
         rtype="Provenance", fact_ids=[])

    # ---- sections ----
    sections = []
    adv = by_kind.get("advice_text") or []
    section_specs = [
        ("Chief complaints", "Chief complaints", by_kind.get("complaints")),
        ("Medical history / Diagnosis", "Diagnosis", by_kind.get("diagnoses")),
        ("Investigations", "Diagnostic studies", by_kind.get("investigations")),
        ("Medications", "Medication summary", by_kind.get("medications")),
        ("Procedures", "History of past procedure", by_kind.get("procedures")),
        ("Allergies", "Allergy record", by_kind.get("allergies")),
        ("Vital signs", "Vital signs", by_kind.get("vitals")),
        ("Document reference", "Clinical document", [u_doc]),
    ]
    for title_s, code_s, urns in section_specs:
        if urns:
            sections.append(R.section(title_s, code_s, urns))
    if adv:
        sections.append(R.section("Follow up / Advisory notes", "Plan of care",
                                  [], text_div="<p>" + "</p><p>".join(adv) + "</p>"))

    u_comp = R.new_urn()
    comp = R.composition(
        urn=u_comp, profile=profile, type_coding=type_coding, title=title,
        patient_ref=pat_ref, author_refs=[u_prac], enc_ref=enc_ref,
        sections=sections, date=when, custodian_ref=u_org,
    )
    entries.insert(0, (u_comp, comp))

    ident = hashlib.sha1(f"{document_id}:{doc_type}".encode()).hexdigest()[:16]
    bundle = R.bundle_document(u_bundle, entries, when, ident)
    bundle_hash = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, default=str).encode()
    ).hexdigest()

    issues = _lint(bundle)
    held_summary = [
        {"fact_type": f["fact_type"], "text": f["local_text"],
         "review_state": f.get("review_state"),
         "confidence": float(f["confidence_overall"]) if f.get("confidence_overall") is not None else None,
         "note": f.get("review_note")}
        for f in held
    ]
    for h in held_summary:
        issues.append({"severity": "info", "code": "held-for-review",
                       "msg": f"{h['fact_type']} '{h['text']}' not asserted (review_state={h['review_state']})"})
    n_err = sum(1 for i in issues if i.get("severity") == "error")
    if n_err:
        bundle_status, vstatus = "draft", "error"
    elif held:
        bundle_status, vstatus = "draft", "warning"        # incomplete until review
    else:
        bundle_status, vstatus = "ready_to_share", "valid"  # structurally clean, fully governed

    if persist:
        with session_scope() as sess:
            for rr in resource_rows:
                repo.upsert_fhir_resource(
                    sess, patient_id=pid, encounter_id=eid,
                    resource_type=rr["rtype"], fhir_id=rr["fhir_id"],
                    profile=rr["profile"], resource=rr["resource"],
                    derived_from_facts=rr["facts"],
                    validation_status=vstatus, validation_issues=issues,
                )
            repo.upsert_fhir_resource(
                sess, patient_id=pid, encounter_id=eid, resource_type="Composition",
                fhir_id=comp["id"], profile=comp["meta"]["profile"], resource=comp,
                derived_from_facts=[], validation_status=vstatus, validation_issues=issues,
            )
            bundle_id = repo.insert_fhir_bundle(
                sess, patient_id=pid, encounter_id=eid, artifact_type=profile,
                bundle=bundle, bundle_hash=bundle_hash, ig_package=settings.ig_package,
                validation_status=vstatus, status=bundle_status,
            )
            repo.finish_pipeline_run(sess, run_id, status="ok",
                                     metrics={"artifact": profile, "resources": len(entries),
                                              "asserted_facts": len(facts),
                                              "held_facts": len(held),
                                              "errors": n_err, "bundle_status": bundle_status})
            repo.set_document_status(sess, document_id, "projected")
            repo.write_audit(sess, actor="fhir-svc", action="create", entity="fhir_bundle",
                             entity_id=str(bundle_id), patient_id=str(pid) if pid else None,
                             detail={"artifact": profile, "asserted": len(facts),
                                     "held": len(held), "status": bundle_status})

    log.info("projected", document_id=document_id, artifact=profile,
             asserted=len(facts), held=len(held), errors=n_err, status=bundle_status)
    return {"artifact_type": profile, "document_id": document_id, "doc_type": doc_type,
            "bundle": bundle, "bundle_hash": bundle_hash, "issues": issues,
            "bundle_status": bundle_status, "asserted_facts": len(facts),
            "held_facts": held_summary, "resource_count": len(entries)}


def _lint(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Lightweight structural checks (full HAPI/IG validation is a follow-up)."""
    issues: list[dict[str, Any]] = []
    urls = {e["fullUrl"] for e in bundle["entry"]}
    if bundle["entry"][0]["resource"]["resourceType"] != "Composition":
        issues.append({"severity": "error", "msg": "first entry must be Composition"})

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            ref = o.get("reference")
            if isinstance(ref, str) and ref.startswith("urn:uuid:") and ref not in urls:
                issues.append({"severity": "error", "msg": f"dangling reference {ref}"})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(bundle)
    for e in bundle["entry"]:
        r = e["resource"]
        if r["resourceType"] in ("Observation",) and "valueQuantity" not in r and "valueString" not in r:
            issues.append({"severity": "warning",
                           "msg": f"Observation {r['id']} has no value"})
        if r["resourceType"] == "Condition" and not r.get("code", {}).get("coding"):
            issues.append({"severity": "info",
                           "msg": f"Condition {r['id']} not coded (local text only)"})
    return issues


def project_patient(patient_id: str | UUID) -> dict[str, Any]:
    patient_id = str(patient_id)
    with session_scope() as sess:
        prow = sess.execute(text("SELECT * FROM patient_identity WHERE id=:i"),
                            {"i": patient_id}).mappings().first()
        if not prow:
            raise ValueError(f"patient {patient_id} not found")
        prow = dict(prow)
        docs = sess.execute(
            text("""SELECT sd.id::text AS id, sd.original_filename, sd.status,
                           dc.doc_type
                    FROM source_document sd
                    JOIN clinical_fact cf ON sd.id = ANY(cf.source_doc_ids)
                    LEFT JOIN doc_classification dc ON dc.document_id = sd.id
                    WHERE cf.patient_id = :p
                    GROUP BY sd.id, sd.original_filename, sd.status, dc.doc_type
                    ORDER BY sd.ingested_at"""),
            {"p": patient_id},
        ).mappings().all()

    bundles = []
    ready = 0
    for d in docs:
        res = project_document(d["id"])
        if res["bundle_status"] == "ready_to_share":
            ready += 1
        bundles.append({"document_id": d["id"], "filename": d["original_filename"],
                        "doc_type": res["doc_type"], "artifact_type": res["artifact_type"],
                        "bundle_status": res["bundle_status"],
                        "asserted_facts": res["asserted_facts"],
                        "held_facts": res["held_facts"],
                        "issues": res["issues"], "bundle": res["bundle"]})

    return {
        "patient": {
            "id": patient_id,
            "mpi_id": prow.get("mpi_id"),
            "name": (prow.get("name_full")
                     or " ".join(x for x in [prow.get("name_given"), prow.get("name_family")] if x)
                     or None),
            "sex": prow.get("gender"),
            "birth_date": str(prow["birth_date"])[:10] if prow.get("birth_date") else None,
            "age_years": prow.get("age_years"),
            "abha_number": prow.get("abha_number"),
            "abha_address": prow.get("abha_address"),
            "identity_confidence": (float(prow["identity_confidence"])
                                    if prow.get("identity_confidence") is not None else None),
        },
        "ig_package": settings.ig_package,
        "artifact_count": len(bundles),
        "ready_to_share": ready,
        "needs_review": len(bundles) - ready,
        "bundles": bundles,
    }
