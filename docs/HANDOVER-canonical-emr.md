# HANDOVER — Canonical longitudinal EMR + worklist/workbasket review + ABDM/FHIR

**For a fresh Claude Code session.** Start a new conversation, read this file first,
then read `docs/ARCHITECTURE.md` (as-built) and `docs/DESIGN.md` (north-star +
10-country research). This document defines the next milestone and everything you
need to pick it up cold.

---

## STATUS — a first working vertical slice is BUILT (2026-09-09, commit 4924459)

Done and verified end-to-end on the pod:

- **Migration `0004_canonical_emr`** — the canonical model: 39 tables across
  `cn_*` (Layer 1 clinical, the 50-domain model), `ops_*` / `fin_*` (Layer 2,
  skeletal), `ai_*` (AI provenance layer), `rv_*` (workbasket/worklist), plus
  `cn_fhir_bundle`.
- **`src/cdi_adapter/canon/promote.py`** — APPROVED `rv_item` → `cn_*` rows +
  one `cn_provenance` row per resource + the `ai_event → ai_suggestion →
  ai_clinical_verification` chain + `cn_audit_event`. Idempotent per item.
- **`src/cdi_adapter/webapp/reviewer.py` + `reviewer_page.py`** — the reviewer
  app at **`/reviewer`** (own route tree, `/api/reviewer/*`): workbasket → claim
  into worklist → per-element accept/correct/reject → approve → promote → build
  bundle → validate. `rv_item` rows are seeded when a job reaches `review`.
- **`src/cdi_adapter/fhir/canonical.py`** — ABDM `Bundle(type=document)` from
  canonical rows: Composition first with profile sections, Patient carrying ABHA
  **and** a distinct MRN identifier, INPS dedicated vital-sign profiles
  (`ObservationBP`, `ObservationBodyWeight`, …), MedicationRequest / Condition /
  Observation / DiagnosticReport / Procedure / AllergyIntolerance.
- **`src/cdi_adapter/fhir/validate_abdm.py`** — HL7 `org.hl7.fhir` validator CLI
  with the `nrces.fhir.r4.ndhm` IG **if `java` + `CDI_FHIR_VALIDATOR_JAR` are
  present** (they are not on the pod yet), else a Python conformance checker
  (Bundle.type, Composition-first, reference resolution, ABHA + MRN identifier
  rule, required resources per artifact, profile assertion, Observation value).

Verified runs: a 3-drug prescription for an existing registry patient (has ABHA)
→ promote (3 MedicationRequest + 1 Condition + 3 Observation) → PrescriptionRecord
bundle with **both** ABHA + MRN identifiers → `python-conformance` **ok, zero
issues, ready_to_share**. A lab report → DiagnosticReportRecord (6 lab_result +
1 diagnostic_report + 6 Observations) → **ok, ready_to_share**.

### Still to do (the rest of the milestone)

1. **Real HL7 validation** — put a JRE + `validator_cli.jar` on the pod
   (`/workspace/tools/validator_cli.jar`, `CDI_FHIR_VALIDATOR_JAR` env) and the
   `nrces.fhir.r4.ndhm#7.x` package; `validate_abdm.validate()` already prefers it.
2. **Composition sections per artifact** — `fhir/canonical._SECTIONS` covers the
   common artifacts but PrescriptionRecord currently only emits a Medications
   section, so Condition/Observation ride along unreferenced. Flesh out the
   section maps against the live v7 profiles (and INPS).
3. **Terminology** — promote copies the seed-map codes onto `cn_*` rows; wire the
   real SNOMED/LOINC/ICD service (gap #2) so codes are bound, not just carried.
4. **Encounter richness, auth, INPS profiles, ops/finance wiring** — see §6.
5. **Voice→EMR** — the structured `cn_clinical_note` (SOAP) is modelled but not
   yet fed; the Bengali/Hindi voice path writes here.

The rest of this document is the original spec — still the reference for #1–#5.

---

## 0. TL;DR of the ask

Build a **production-grade canonical longitudinal clinical data model** as the
system of record, with **FHIR R4 + ABDM v7** as an *interoperability layer on
top* — not as the database design. Then:

1. **Persist only human-reviewed data** into the canonical model.
2. **Human-in-the-loop lives on its own link** (separate app/route), built as a
   **workbasket → worklist** flow: unassigned reviewable items sit in a
   *workbasket*; a reviewer *picks* an item into their *worklist* (claims it),
   works it (view scan ↔ structured data, edit, approve/reject per element), and
   on approval the item is **promoted to the canonical EMR** and an **ABDM FHIR
   `Bundle(type=document)` is generated**.
3. **Validate** that generated bundles actually conform to the current ABDM FHIR
   IG (`nrces.fhir.r4.ndhm`, v7.x) and that the ABHA identifier is represented
   correctly — with a real validator, not the current hand-rolled lint.

The governing principle stays: *AI proposes, evidence proves, deterministic rules
govern, humans adjudicate, FHIR represents the governed result.* The VLM is never
the system of record.

---

## 1. Where things stand right now (2026-09-09)

### Repo
`D:\Claude\clinical-emr-adapter` — Python 3.12, FastAPI, SQLAlchemy 2 / psycopg 3,
Postgres + MinIO + Redis, Alembic. GitHub: `https://github.com/sayantanIFAI/emr`
(branch `main`). Latest commits at handover time:

```
434f772 chore(mlserve): make vllm guided decoding toggleable
5d1f7e9 fix(mlserve): drop strict response_format for vllm guided decoding
95f9246 feat(jobs): fan out phase-2 extract for the vllm backend (Path B)
fdb0715 feat(mlserve): vllm backend with XGrammar guided JSON decoding
edc2289 perf(cpu): cap native thread pools to the container's real CPU budget
538c080 Speed: scored heuristic classifier, relaxed schemas, fewer extract retries
```

### Live pod
- **URL:** `https://g1a7lswmb5t79o-8888.proxy.runpod.net/` — `/` upload → FHIR,
  `/review` the current adjudication console.
- Pod `g1a7lswmb5t79o`, RTX PRO 4500 Blackwell 32 GB, no Docker.
- **Model backend is `hf`** (`transformers` Qwen2.5-VL-7B). A vLLM 0.28 spike was
  done and **reverted** — see §7. `hf` restart: `bash /workspace/run_mlserve.sh`.
- One entrypoint after a pod restart: `bash /workspace/cdi/infra/runpod/start_all.sh`.
- SSH rules (**strict**): `ssh root@216.81.151.61 -p 11431 -i ~/.ssh/id_ed25519`
  — never `ssh.runpod.io`, never `-t`/`-tt`, keep commands < ~10 s or use
  `setsid nohup … </dev/null &`. Port/IP change on every new pod; ask the user.

### What already works end-to-end (reusable)
Pipeline `ingest → OCR(rapidocr) → classify(heuristic) → extract(VLM) →
terminology(seed map) → validate + S6 gate → FHIR projection`, plus a `/review`
console. A 3-doc job ≈ 74 s. Extraction is provenance-bound (pixel bbox + OCR
span + model version + confidence). See `ARCHITECTURE.md` §5 for stage detail.

### Current data model (what the canonical model must supersede/absorb)
`db/alembic/versions/0001_initial_schema.py` + `0002_patient_mpi.py` +
`0003_patient_registry.py`. Core today:
- `patient_identity` (MPI, `mpi_id = CFP-YYYY-NNNNNN`), `patient_identity_alias`,
  `patient_registry` (clinic master: name/mobile/dob/gender/address/abha_id).
- `source_document`, `document_page`, `ocr_block`, `doc_classification`,
  `extraction`, `pipeline_run`.
- `clinical_fact` (EAV: `fact_type`, coded value, `review_state`,
  `source_doc_ids[]`, confidence), `medication_detail`, `fact_provenance`
  (`bbox_union`, evidence block ids), `encounter` (thin: class AMB/IMP, period,
  `derived_from[]`), `fact_conflict`, `review_task`, `review_note`, `audit_log`.
- `fhir_resource`, `fhir_bundle` (status `ready_to_share | draft`).

This is a **document-extraction staging model**, not a longitudinal EMR. The
canonical model in §3 is the target; `clinical_fact` becomes the *pre-promotion
staging* layer feeding it.

---

## 2. Architecture the user wants (three layers, kept separate)

```
                 CANONICAL EMR  (system of record, Postgres)
                         │
        ┌────────────────┼─────────────────┐
        │                │                 │
  Layer 1              Layer 2           (Layer 3 is not a DB)
  CLINICAL TRUTH        HOSPITAL OPS
  Patient / MPI         Appointment / Queue
  Encounter            Bed / Ward / Transfer
  Condition            Staff / Roster
  Observation          Inventory
  Medication{Master,   Billing: Charge / Invoice /
    Order, Admin,        Payment / Claim / Coverage
    Dispense}
  Lab{Order,Specimen,
    Result} / DiagnosticReport
  ImagingStudy / Procedure
  CarePlan / Goal / Referral
  Allergy / Nursing{Assessment,Event}
  Document / Consent / Provenance / Audit
  Device / DeviceObservation / Alert
        │
        ▼
  AI LAYER  (never writes clinical tables directly)
  ai_event  →  ai_suggestion  →  clinical_verification  →  (promotes to Layer 1)
        │
        ▼
  INTEROPERABILITY LAYER  (generated on demand, not stored as the model)
        │
   ┌────┴─────────────┐
   │                  │
 FHIR R4          ABDM v7 IG  (nrces.fhir.r4.ndhm)
 (org.hl7.fhir)   Bundle(type=document) + Composition + INPS
   │                  │
   └────────┬─────────┘
       External systems / HIE
```

**Non-negotiable design rules** (from the user's spec):
- `patient_id = UUID`, immutable, is the canonical key. **ABHA is never a primary
  key** — it's one external identifier among many (`identifier[]`: ABHA, MRN,
  insurance member id, local ids…).
- **Patient Master is separate from Encounter.** One patient → many encounters
  (OPD #1, OPD #2, ER, IPD admission with ward/ICU/discharge, diagnostic,
  telemedicine).
- **Problem list vs encounter diagnosis** are different: `condition.category ∈
  {PROBLEM, ENCOUNTER_DIAGNOSIS, HEALTH_CONCERN}`. A longitudinal problem
  (Diabetes) is not the same row as today's Dx (Acute viral fever).
- **Chief complaint ≠ Condition.** "Fever ×3 days" (presenting complaint) is not
  a diagnosed `Condition`; only the physician-asserted "Viral fever" is. Model
  the complaint in the clinical-note `subjective` / as an `Observation` or a
  complaint entity, not by minting a `Condition`.
- **Medication has four distinct states**: `medication_master` (drug catalogue) →
  `medication_order` (prescribed) → `medication_dispense` (pharmacy) →
  `medication_administration` (given, inpatient). prescribed ≠ dispensed ≠
  administered ≠ taken.
- **Lab is a chain**: `lab_order → specimen → lab_result → diagnostic_report`.
- **Imaging**: the EMR stores `imaging_study` *metadata + references* (accession,
  DICOM UID, modality, body site, report). PACS owns the pixels — no DICOM blobs
  in the EMR.
- **Clinical documentation is structured** (SOAP): `clinical_note` with
  `subjective{chief_complaint, hpi, past/surgical/family/social/medication
  history}`, `objective{vitals, exam, observations}`, `assessment{diagnosis[],
  ddx[], risk[]}`, `plan{med_orders[], investigations[], procedures[],
  referrals[], follow_up}`, plus `free_text`, `signed_at/by`, `version`. The
  Bengali/Hindi voice→EMR path feeds *this*, not a text blob.
- **Provenance is first-class** on every clinical value: source (nurse / doctor /
  device / AI), method, device id, timestamp, verified-by, and — for AI —
  `model_id`, `model_version`, `prompt_version`, `human_verified`.
- **Consent + Audit** are mandatory tables (`consent`: purpose/scope/recipient/
  period/status; `audit_event`: user/patient/resource/action ∈ CRUD+EXPORT/
  timestamp/source/ip).
- **Billing/financial data stays out of the clinical tables** (`charge`,
  `invoice`, `payment`, `refund`, `receipt`, `claim`, `coverage`,
  `preauthorization`).

---

## 3. The 50 canonical domains to model (logical data model)

Group them into schemas: `clinical`, `ops`, `ai`, `interop`, `admin`.

| # | Domain | Notes / key fields |
|---|---|---|
| 01 | Patient / MPI | UUID pk; `enterprise_patient_id` (EMPI); name(prefix/first/middle/last); dob; gender; sex_at_birth; marital_status; blood_group; deceased{status,datetime}; status; created/updated |
| 02 | External identifiers | `identifier[]` → ABHA, ABHA address, MRN[], insurance_member_id, local; `type` coded; ABHA identifier type per NRCeS |
| 03 | Practitioner | `hpr_id`, registration_number, qualification[], specialty[], license_status, signature |
| 04 | PractitionerRole | practitioner × organization × role × period (same doctor, many roles) |
| 05 | Organization | type, registration, NABH/NABL, parent_organization |
| 06 | Location | organization_id, building/floor/department/ward/room/bed, geo |
| 07 | Encounter | type ∈ {OPD,IPD,ER,ICU,DAYCARE,TELEMEDICINE,DIAGNOSTIC}; status; priority; service; specialty; department; attending + consulting[] + care_team[]; location/room/bed; appointment_id; reason/chief_complaint; admission/discharge datetime + source/destination; insurance; encounter_diagnosis[] |
| 08 | Appointment | slot, status, service, practitioner, patient, encounter link |
| 09 | Clinical Note | SOAP structure (see §2); note_type; author_id/role; signed_at/by; version |
| 10 | Chief Complaint | complaint text + duration + onset; linked to note/encounter; NOT a Condition |
| 11 | History | past/surgical/family/social/medication history entries |
| 12 | Examination | system-wise physical exam findings |
| 13 | Vital Signs | as Observation subtype (category `vital-sign`); use INPS dedicated profiles on export |
| 14 | Observation | category ∈ {vital-sign,laboratory,examination,social-history,other}; code(LOINC/SNOMED); value+unit(UCUM); reference_range; interpretation; effective/issued datetime; performer; device_id; method |
| 15 | Condition / Problem | category ∈ {PROBLEM,ENCOUNTER_DIAGNOSIS,HEALTH_CONCERN}; code_system SNOMED_CT/ICD_10/local; clinical_status; verification_status; severity; onset/recorded/resolved dates; recorder; asserter |
| 16 | Allergy / Intolerance | substance; category {medication,food,environmental}; reaction[]{manifestation,severity,onset}; criticality; verification/clinical status. High-safety dataset |
| 17 | Medication Master | generic/brand name; strength; dosage_form; route; manufacturer; code (RxNorm-equiv / local); formulary |
| 18 | Medication Order | status; intent; dose+unit; route; frequency; timing; duration; quantity; refills; start/end; prescriber; indication; instructions; substitution_allowed |
| 19 | Medication Administration | order_id; administered_by; scheduled vs actual time; dose_given; route; status; reason_not_given |
| 20 | Medication Dispense | order_id; pharmacy; quantity; days_supply; when_handed_over |
| 21 | Lab Order | ordered_by; priority; requested_test[]; clinical_indication |
| 22 | Specimen | order_id; specimen_type; collection datetime/collector/site; accession_number; status |
| 23 | Lab Result | specimen_id; test_code/name; value+unit; reference_range; abnormal_flag; interpretation; performed/verified datetime; verified_by; instrument_id |
| 24 | Diagnostic Report | order_id; specimen[]; result[]; performer; interpreter; conclusion; report_status; report_document ref |
| 25 | Imaging Study | accession_number; modality; body_site; study_datetime; performing_department; DICOM_UID; series[]; radiologist; findings; impression; report_document ref (metadata only) |
| 26 | Procedure | code/name; status; performed_datetime; performer[]; location; body_site; indication; findings; complications; outcome; anesthesia; devices_used[]; report |
| 27 | Care Plan | status; period; author; problems[]; goals[]; interventions[]; medications[]; investigations[]; referrals[]; follow_up[] |
| 28 | Goal | care_plan_id; target measure + value + date; status |
| 29 | Referral | referring/referred-to doctor + department; reason; urgency; clinical_summary; requested_service; status; appointment; outcome |
| 30 | Nursing Assessment | consciousness; pain; mobility; nutrition; skin; fall_risk; pressure_ulcer_risk; intake_output; vitals; notes |
| 31 | Nursing Event | med_admin / IV / catheter / wound / dressing / observation / escalation / handover |
| 32 | Admission | admission datetime/type; admitting doctor/department; ward/room/bed; diagnosis; payer; transfer_history[]; discharge datetime |
| 33 | Bed / Ward / Room | ward → room → bed hierarchy; `bed_status ∈ {AVAILABLE,OCCUPIED,CLEANING,MAINTENANCE,RESERVED,BLOCKED}` |
| 34 | Transfer | admission_id; from/to location; datetime; reason |
| 35 | Discharge | encounter_id; datetime; disposition; discharge_diagnosis[]; discharge_meds[]; instructions; follow_up |
| 36 | Document | document_type; title; author; created/service datetime; mime_type; storage_uri; hash; version; confidentiality; OCR_text; extracted_structured_data; source |
| 37 | Consent | purpose; scope; recipient; period; status; provision; granted_at; revoked_at |
| 38 | Provenance | target resource; agent{who,role}; activity; entity{source}; datetime; signature |
| 39 | Audit Event | user_id; patient_id; resource_type/id; action ∈ CRUD+EXPORT; timestamp; source_system; ip/device; reason |
| 40 | Device | serial_number; device_type; manufacturer; model; department; location; status; calibration; maintenance; warranty; connectivity |
| 41 | Device Observation | device_id; patient_id?; encounter_id?; timestamp; parameter; value+unit; quality; alarm |
| 42 | Alert | patient/encounter/device; type; severity; status; raised/ack/resolved |
| 43 | Coverage | payer; policy_number; member_id; plan; validity; relationship; authorization; coverage_type |
| 44 | Preauthorization | coverage_id; services[]; requested/approved amount; status |
| 45 | Charge | service + service_code; quantity; unit_price; discount; tax; net_amount; payer |
| 46 | Invoice | charge[]; totals; status |
| 47 | Payment / Refund / Receipt | invoice_id; method; amount; datetime |
| 48 | Claim | payer; preauthorization; services[]; charges; approved/rejected amount; status; settlement |
| 49 | AI Event | patient/encounter/source_document/source_audio ids; task ∈ {transcription,entity_extraction,diagnosis_suggestion,medication_extraction,prescription_OCR,summarization}; model_id/version; prompt_version; policy_version; input; output; confidence; human_review_status; reviewed_by/at; final_clinical_resource_id |
| 50 | AI Suggestion / Clinical Verification | candidate structured value ↔ reviewer decision (accept/correct/reject) ↔ the canonical row it became |

**openEHR note (for depth, not for copying):** openEHR separates a stable
*Reference Model* from *Archetypes* (reusable clinical concepts) and *Templates*
(archetypes composed into forms). We are not adopting openEHR, but the lesson is
the same as the user's: **do not let FHIR be your internal schema.** Keep a
clean internal model; map to FHIR at the edge.

---

## 4. ABDM v7 / FHIR interop layer — get this right

- Target IG: **`nrces.fhir.r4.ndhm`** current (**v7.x**, FHIR R4.0.1). Read the
  live IG before coding — versions have moved; `CDI_IG_PACKAGE` in config points
  at `6.5.0` today and should be bumped.
- **Clinical artifacts (7):** `OPConsultRecord`, `PrescriptionRecord`,
  `DiagnosticReportRecord`, `DischargeSummaryRecord`, `HealthDocumentRecord`,
  `ImmunizationRecord`, `WellnessRecord`. **Billing artifact:** `InvoiceRecord`.
- **v7 additions to support:** the **Indian Patient Summary (INPS)** profile set
  (~28 profiles, ISO 27269-aligned) and **dedicated vital-sign profiles** —
  `ObservationHeartRate`, `ObservationOxygenSat`, `ObservationRespRate`,
  `ObservationBP`, `ObservationBodyTemp`, `ObservationBMI`, `ObservationBodyHeight`,
  `ObservationBodyWeight`, `ObservationHeadCircum` — which **supersede** the
  generic `ObservationVitalSigns`. Map vitals to these on export.
- **Bundle shape:** `Bundle.type = document`, **`Composition` is the first
  entry**, all referenced resources follow as subsequent entries. Composition
  alone is not a document.
- **Composition.type** is a `CodeableConcept`, binding `FHIRDocumentTypeCodes`
  (preferred), SNOMED CT where appropriate. **Do not hard-code the per-artifact
  SNOMED codes** from old examples — resolve them from the current profile /
  examples for the version you target.
- **Composition sections** are profile-defined with cardinalities, slices,
  Must-Support, bindings. E.g. OPConsultRecord sections: Chief Complaints,
  Physical Examination, Allergies, Medical History, Family History, Investigation
  Advice, Medications, FollowUp, Procedure, Referral, Other Observations,
  DocumentReference. The record contains *only clinically applicable* sections —
  "defined" ≠ "always present".
- **Identifiers:** Patient carries `identifier[]` with an `ABHA` type coding
  (per NRCeS identifier terminology) **and** the hospital MRN/OIN as a *separate*
  identifier. They are not interchangeable.
- **Existing code to build on:** `src/cdi_adapter/fhir/resources.py` (plain-dict
  resource builders + `NRCES` profile URLs + `ARTIFACT` map doc_type→profile) and
  `src/cdi_adapter/fhir/service.py` (`project_document` / `project_patient`,
  `_lint`). `_lint` is ~4 hand-rolled checks — **replace with real validation**
  (§6 gap #1).

---

## 5. Human-in-the-loop: workbasket → worklist, on its own link

Today there is a single `/review` page (`webapp/review.py`, `review_page.py`) and
an inline editor in the job flow (`webapp/page.py` → `/api/jobs/{id}/facts` →
`/api/jobs/{id}/generate`). The user wants a **dedicated reviewer app** with the
classic **workbasket/worklist** pattern:

| Concept | Meaning here |
|---|---|
| **Workbasket** | The pool of review items *not yet claimed* — every document/encounter whose extraction produced facts in `review_state = in_review` (or a `_partial` extraction, or a governance-gate `in_review`). Filterable by doc_type, facility, age, priority, patient. |
| **Pick / claim** | A reviewer moves an item from the workbasket into **their worklist** — sets `assignee`, `claimed_at`, locks it so no one else edits. Support release / reassign / steal-after-timeout. |
| **Worklist** | The reviewer's claimed items, in progress. Each opens the review workspace: **scanned page on the left with bbox highlight**, **structured candidate data on the right**, editable per element; per-element **accept / correct / reject**; free-text note; then **Approve** (all elements resolved) or **Send back**. |
| **Approve → promote** | On approve: (a) write the reviewer decision + provenance (`clinical_verification`, `audit_event`), (b) **promote the approved structured data into the canonical EMR** (Layer 1 rows in §3), (c) **generate the ABDM FHIR `Bundle(type=document)`** for the matching artifact, (d) run real IG validation (§6 #1); `bundle.status = ready_to_share` only on a clean validation. |

Build it as its own route tree (e.g. `/rcm` or `/reviewer`, separate templates,
its own nav) and its own API namespace (`/api/workbasket`, `/api/worklist/...`,
`/api/worklist/{item}/approve`). Auth/roles are a known gap (§6 #4) — at minimum
add a reviewer identity header/session now so `assignee` and provenance are real.

Suggested tables (schema `review`):
- `review_item` — id, kind (document|encounter), source_document_id / encounter_id,
  patient_id, doc_type, priority, state ∈ {open,claimed,in_progress,approved,
  rejected,superseded}, assignee, claimed_at, completed_at, sla_due_at.
- `review_item_element` — review_item_id, staging ref (`clinical_fact.id` etc.),
  proposed value, reviewer value, decision ∈ {accept,correct,reject}, note.
- `review_action` — audit of every claim/release/edit/approve with actor+ts.

The existing `review_task` / `review_note` / `clinical_fact.review_state` can be
the element-level substrate; `review_item` is the new claimable unit on top.

---

## 6. Known gaps to close as part of this milestone

1. **Real FHIR/IG validation.** No validator today — `_lint()` is hand-rolled and
   `meta.profile` is asserted, never checked. Add the **Java `org.hl7.fhir`
   validator CLI** (or HAPI) with the `nrces.fhir.r4.ndhm` package as a sidecar /
   subprocess; parse `OperationOutcome`; gate `bundle.status` on zero `error`.
   Needs a JRE on the pod (none today). Python `fhir.resources` can add base-R4
   datatype checks but cannot do profile/slice/invariant conformance.
2. **Terminology is a ~120-entry seed map** (`terminology/seed.py`). For a real
   canonical model you need SNOMED CT (India edition) + LOINC + ICD-10 with
   `$validate-code` / `$translate` and local-code minting. Snowstorm-lite or a
   Postgres-loaded value set + embedding candidate-gen + rule reranker.
3. **Encounter model is thin.** The canonical `encounter` (§3 #07) with type/
   admission/discharge/care-team/bed is much richer than today's AMB/IMP stub.
4. **AuthN/AuthZ + DPDP.** No auth anywhere. The reviewer app needs real
   identity, roles, and an audit trail that names a person.
5. **Single pod, in-memory jobs.** Jobs live in a module dict (`webapp/jobs.py`)
   → lost on webapp restart. A durable queue is Roadmap #8.
6. **INPS + dedicated vital-sign profiles** (§4) are not implemented.

`ARCHITECTURE.md` §13/§14 has the full gap list and roadmap; align with it.

---

## 7. The vLLM spike (done and reverted — context so you don't redo it)

A guarded spike installed **vLLM 0.28 + XGrammar** in an isolated
`/workspace/vllm-venv` (torch 2.13+cu130, sm_120) and added a `vllm` model-gateway
backend (`src/cdi_adapter/mlserve/backends.py::VLLMBackend`, `bundle_schema()`),
guided-JSON decoding, and a **Phase-2 extract fan-out** in `webapp/jobs.py`
(`_extract_sem`, `_stage2_pool`, `extract_concurrency`). Infra:
`infra/runpod/start_vllm.sh`, `/workspace/run_vllm.sh`.

**Measured result: no latency improvement** on this GPU. 3-doc job stayed ~72–74 s.
Per-request 7B inference on this workstation Blackwell (165 W, FlashInfer
sampler disabled → FlashAttention-2 fallback) is not faster than `hf`; XGrammar
*added* ~50 % per-token overhead (retries were already ~zero after the schema
relaxation); the fan-out overlaps only 2 of 3 docs (doc 1 runs alone to pin the
new-patient identity). The real latency lever remains a smaller fine-tuned DSLM —
see `ARCHITECTURE.md` §7.4.

**Current state:** `.env` has `CDI_MLSERVE_BACKEND=hf`, vLLM serve is stopped,
GPU free for `hf`. The `vllm` backend code stays in the tree (dormant, toggle via
`CDI_MLSERVE_BACKEND=vllm` + `bash infra/runpod/start_vllm.sh`) — do **not**
delete it; it's the right foundation for real batching once concurrency or a
smaller model makes it pay. The Phase-2 fan-out is gated by `extract_concurrency`
(default 4) but with `hf` you should set `CDI_EXTRACT_CONCURRENCY=1` to keep
serial behaviour (concurrent `transformers.generate` on one model OOMs).

---

## 8. Suggested plan of work (for the new session to refine with the user)

1. **Model** — write `db/alembic/versions/0004_canonical_emr.py` (or a fresh
   schema module) for the 50 domains in §3, grouped into `clinical` / `ops` /
   `ai` / `interop` / `admin` schemas. Keep `clinical_fact` + friends as the
   **staging** layer; add `final_resource_id` links from staging → canonical.
2. **Promotion** — a `promote/` service: given an approved `review_item`, map its
   accepted elements to canonical rows (Patient/Encounter/Condition/Observation/
   MedicationOrder/…) with full provenance, idempotently.
3. **Reviewer app** — the `/reviewer` route tree + `review` schema tables in §5;
   workbasket list, claim/release, review workspace (reuse the image+bbox+table
   editor from `webapp/page.py`), approve → promote → project → validate.
4. **Interop** — extend `fhir/resources.py` + `fhir/service.py` to build each of
   the 7 clinical artifacts + `InvoiceRecord` from **canonical** rows (not from
   `clinical_fact`), add INPS + dedicated vital-sign profiles, bump the IG
   package, and wire the **real validator** (§6 #1).
5. **Validate the loop** — golden case: upload the 5 sample docs → review →
   approve → canonical rows exist → bundle generated → **validator returns zero
   errors against `nrces.fhir.r4.ndhm` v7** → ABHA + MRN both present as distinct
   identifiers. Automate this as a test.

Do §1–§2 first (model + promotion) since everything else depends on the canonical
tables existing.

---

## 9. Operational cheatsheet

```bash
# pod (ask user for current IP/port; rules in §1)
ssh root@216.81.151.61 -p 11431 -i ~/.ssh/id_ed25519

# deploy loop
cd /workspace/cdi && git fetch -q origin main && git checkout -q -B main origin/main
.venv/bin/python -m pytest -q
bash /workspace/run_mlserve.sh      # hf gateway  :8077  (detached wrapper on pod)
bash /workspace/run_webapp.sh       # web app     :8888  (the RunPod-proxied port)
# full cold boot:  bash /workspace/cdi/infra/runpod/start_all.sh

# DB
export PGPASSWORD=cdi
psql -h 127.0.0.1 -U cdi -d cdi
# test-data reset (keeps the 4 seed registry patients):
psql -h 127.0.0.1 -U cdi -d cdi -f /workspace/clean_uploads.sql

# sample docs generator on the pod:  /workspace/mkdocs.py  → /workspace/data/inbox/*.pdf
```

Git: commit as `sayantanIFAI <samya51289@gmail.com>`, end messages with
`Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`; PR bodies end with
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`. The legacy HMS
DB is read-only and must never be modified.
