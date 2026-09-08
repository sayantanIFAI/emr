# CDI-Adapter — As-Built Design, Architecture & Implementation

**Clinical Document Intelligence Adapter** — turns a legacy hospital's *scanned-only*
records into a structured EMR and **ABDM/ABHA FHIR R4** record bundles.

- **Status:** working end-to-end prototype with a governance gate, deployed and live
  on a RunPod GPU pod. Not production-hardened (see §13).
- **Live URL:** `https://2kkk36y0r2z7yv-8888.proxy.runpod.net` — `/` upload → FHIR,
  `/review` the human adjudication queue.
- **Companion docs:** [`DESIGN.md`](DESIGN.md) is the north-star design + 10-country
  research; this document is **what is actually built and running**.
- **Governing principle** (adopted verbatim from external review):
  > *AI proposes clinical facts. Evidence proves them. Deterministic validation
  > governs them. Human adjudication resolves uncertainty. FHIR represents the
  > governed result.*
  The VLM is never the system of record. Every clinical fact is bound to a pixel
  region, an OCR span, a model version and a confidence score; a deterministic
  rules engine (S6) then routes each fact to **auto-accept** or **human review**,
  and FHIR projection (S9) **only asserts governed facts**.

---

## 0. What exists today (one-paragraph truth)

A pipeline (`ingest → classify → OCR → extract → terminology → **validate/gate** →
FHIR projection`, with a **human review** loop) runs on one GPU pod. Classification
and extraction use a **real open-source vision-language model
(Qwen2.5-VL-7B-Instruct)** served by an in-house model gateway; printed-text OCR
uses RapidOCR (ONNX, CPU); handwriting uses the VLM. Terminology binding uses a
**curated seed map** (SNOMED CT / LOINC / UCUM), not a full terminology server.
**S6** runs deterministic clinical-plausibility rules (physiological ranges, unit
sanity, dose/frequency ceilings, impossible dates, evidence-present,
duplicate/contradiction detection) and a **governance gate** that routes every
fact to `auto_accepted` or `in_review` — a `_partial`/malformed or low-confidence
extraction is **never** auto-trusted. **S8** is a web review console
(image → highlighted bbox → OCR → fact → code → confidence → accept/correct/reject)
that writes reviewer-signed provenance. FHIR projection is deterministic, **asserts
only governed facts**, and emits NRCeS/ABDM `Composition`-based
`Bundle(type=document)` artifacts (`status = ready_to_share` only when fully
governed and structurally clean, else `draft`) with the original scan attached and
a `Provenance` chain. **Still not built:** the fine-tuned domain SLM,
Snowstorm/embedding terminology, HAPI IG validation, S7 longitudinal
reconciliation, ABDM gateway, auth/DPDP, HA/DR — see §13.

---

## 1. Context & problem

The hospital's legacy HMS stores **no discrete clinical data** — every patient
record is a pile of scanned images / PDFs (prescriptions, lab reports, radiology,
discharge summaries, advice slips, vitals sheets). To join ABDM as a Health
Information Provider the hospital must expose FHIR R4 record artifacts under
patient consent. The adapter synthesizes the missing structured record from the
scans **without modifying the legacy database**.

Constraints that shaped the build:

| Constraint | Consequence |
|---|---|
| Open-source only, no per-document API cost | On-prem models: Qwen2.5-VL (Apache-2.0), RapidOCR, seed terminology |
| Runs on a single RunPod pod, **no Docker** | Native processes (Postgres, Redis, MinIO, gateway, web app) |
| Pod GPU is 24–32 GB (Blackwell) | 7B VLM in bf16 fits; no fine-tuned 8B DSLM yet |
| `/workspace` is MooseFS (persistent, but `chown`/`fallocate` fail, dirs forced 0777) | Postgres cluster runs on the ephemeral overlay; a `pg_dump` on `/workspace` + auto-restore is the persistence mechanism |
| Pod is recreated often (new IP/port each time) | One idempotent `start_all.sh`; nothing derived stored only in the cluster |

---

## 2. Solution overview — the adapter principle

```
┌───────────────────────────────┐        read-only         ┌────────────────────────┐
│  LEGACY HMS  +  its database   │ ───────────────────────▶ │      CDI-Adapter        │
│  (scanned documents only)      │   folder / API / batch   │  own PostgreSQL + MinIO │
└───────────────────────────────┘                          └───────────┬────────────┘
                                                                       │
                          ┌────────────────────────────────────────────┴───────────┐
                          ▼                                                         ▼
                  rows in tables                                        ABDM FHIR R4 JSON
        clinical_fact · encounter · fhir_resource                Bundle(type=document) per
        (+ provenance, confidence, review_state)                 source doc → HIP/HRP gateway
```

- The legacy DB is **never written**. The adapter has its own store.
- Two synchronized outputs per document: **relational rows** and a **FHIR bundle**.
- Portability: every jurisdiction difference is isolated into two swappable
  packages — a **Terminology Package** and a **Profile/IG Package** (`CDI_IG_PACKAGE`,
  `CDI_TERMINOLOGY_PACKAGE`). India (`nrces.fhir.r4.ndhm#6.5.0`) is the default.

---

## 3. Architecture

### 3.1 Component view (as deployed)

```
                       ┌──────────────────────────────────────────────────────────┐
   browser ── HTTPS ──▶ │  RunPod edge proxy  :8888  →  pod localhost:8888         │
                       └───────────────────────────┬──────────────────────────────┘
                                                   ▼
                         ┌──────────────────────────────────────────┐
                         │  cdi_adapter.webapp   (FastAPI, uvicorn)  │   port 8888
                         │  - GET /                upload page       │
                         │  - POST /api/jobs       start a job       │
                         │  - GET  /api/jobs/{id}[ /fhir | /download]│
                         │  - GET  /api/patients/{id}/fhir           │
                         │  ThreadPool(1) ── runs the pipeline ──────┼──┐
                         └──────────────────────────────────────────┘  │
                                                                       │ in-process calls
   ┌───────────────────────────────────────────────────────────────────┘
   ▼
 S1 ingest ─▶ S2 classify ─▶ S3 OCR ─▶ S4 extract ─▶ S5 terminology ─▶ S9 FHIR projection
   │             │              │          │              │                  │
   │             │  HTTP        │  HTTP    │  HTTP        │ (in-proc)        │ (in-proc)
   │             ▼              ▼          ▼              ▼                  ▼
   │      ┌───────────────────────────────────────┐   seed.py map      resources.py
   │      │ cdi_adapter.mlserve  (FastAPI)  :8077  │   (SNOMED/LOINC)   deterministic
   │      │  backend = hf → transformers          │
   │      │  Qwen/Qwen2.5-VL-7B-Instruct (bf16)   │  ~16 GB VRAM, lazy-load ~35 s
   │      │  POST /vlm/generate {image_b64,prompt,│
   │      │        json_schema} → {text}          │
   │      └───────────────────────────────────────┘
   │
   ▼ (all stages)
 ┌─────────────┐   ┌──────────────┐   ┌─────────────────────────────┐
 │ PostgreSQL  │   │ MinIO (S3)   │   │ Redis (Celery broker;       │
 │ 16          │   │ scans+pages  │   │ used by the folder-watch    │
 │ (overlay)   │   │ (/workspace) │   │ path, not the web app)      │
 └─────────────┘   └──────────────┘   └─────────────────────────────┘
```

Optional / not on the hot path: `cdi_adapter.worker` (Celery) chains the same
stages for the **folder-watch** ingestion path; `cdi_adapter.api` is the original
ingest-only API; `cdi_adapter.ingest.watcher` watches a drop folder.

### 3.2 The 9-stage pipeline (as built)

| # | Stage | Module | Model / method | Writes |
|---|---|---|---|---|
| S1 | Ingest & normalize | `ingest/pages.py`, `ingest/service.py` | PyMuPDF render; OpenCV deskew (min-area-rect) + denoise + CLAHE; SHA-256 dedupe | `source_document`, `document_page`, original+pages → MinIO |
| S2 | Classify | `classify/service.py`, `classify/prompt.py` | Qwen2.5-VL, schema `classification.v1`, + a fast RapidOCR text hint | `doc_classification` |
| S3 | OCR / layout | `ocr/service.py`, `ocr/rapid.py`, `ocr/vlm_ocr.py` | printed → RapidOCR (boxes+conf+reading order); handwritten → VLM transcription | `ocr_block` |
| S4 | Extract | `extract/service.py`, `extract/prompt.py` | Qwen2.5-VL, schema-locked JSON per `doc_type`, evidence = OCR block ids; **repair + lenient fallback** (marks `_partial`) | `extraction`, `clinical_fact` (+ `medication_detail`), `fact_provenance`, `patient_identity`, `encounter` |
| S5 | Terminology | `terminology/service.py`, `terminology/seed.py` | curated exact + alias + `difflib` fuzzy → SNOMED CT / LOINC; UCUM unit parse; frequency parse | updates `clinical_fact.code_*`, `medication_detail` |
| **S6** | **Validation + gate** | `validate/rules.py`, `validate/service.py` | **deterministic rules** (value ranges, unit sanity, dose/frequency ceilings, impossible dates, evidence-present, unmapped-critical, duplicate & cross-document contradiction) + confidence calibration + **routing** | `clinical_fact.review_state`, `review_note`, calibrated `confidence_overall`, `fact_conflict`, `review_task` |
| S7 | Longitudinal reconciliation | — | **not built** (S6 does per-patient duplicate/contradiction; no temporal merge / summary graph) | — |
| **S8** | **Human adjudication** | `webapp/review.py`, `webapp/review_page.py` | review console: page image + bbox highlight + OCR span + fact + code + confidence + findings → accept / correct / reject; closes `review_task` | `clinical_fact.review_state` (`clinician_confirmed`/`corrected`/`rejected`), reviewer `fact_provenance`, `audit_log` |
| **S9** | **FHIR projection (gated)** | `fhir/service.py`, `fhir/resources.py` | deterministic map → resources → ABDM `Composition` bundle; **asserts only governed facts**; held facts reported, not asserted; structural lint | `fhir_resource`, `fhir_bundle` (`status = ready_to_share` \| `draft`) |

### 3.3 Deployment topology & persistence model

```
POD  (RunPod, RTX PRO 4500 Blackwell 32 GB, Ubuntu 24.04, torch 2.8+cu128, NO Docker)

/  overlay  (30 GB, WIPED on every restart)          /workspace  (MooseFS, PERSISTENT)
├── apt: postgresql-16, redis, cron   ← reinstalled  ├── cdi/            repo + .venv (system-site-packages)
├── /var/lib/postgresql/16/cdi        ← PGDATA,      ├── hf-cache/       Qwen2.5-VL weights (~16 GB)
│      rebuilt + pg_restore on boot                  ├── minio-data/     original scans + page PNGs  ← the irreplaceable bytes
└── running processes (mlserve,webapp)               ├── redis/          AOF
                                                     ├── backup/cdi.dump pg_dump, refreshed by cron every 15 min
                                                     ├── data/inbox…     folder-watch drop dirs
                                                     └── logs/           mlserve.log, webapp.log, bootstrap.log
```

**Why Postgres is on the overlay:** `/workspace` (MooseFS) forces mode 0777 and
rejects `chown`; Postgres refuses such a `PGDATA`. No loop devices, no `/dev/fuse`,
no `CAP_SYS_ADMIN`, so a userspace ext4 image is impossible. The cluster is
therefore ephemeral and **reconstructed from `/workspace/backup/cdi.dump` on each
boot**. Nothing unique is lost: the scan bytes live in MinIO on `/workspace`, and
every derived row is reproducible from those by re-running the pipeline *and* is
captured in the 15-minute dump.

**One-command lifecycle:** `bash /workspace/cdi/infra/runpod/start_all.sh`
→ `bootstrap_pod.sh` (apt, initdb, restore, `alembic upgrade head`, venv) → start
`mlserve` (:8077) → start `webapp` (:8888) → print the live URL.

### 3.4 Model gateway (`cdi_adapter.mlserve`)

A single long-lived process holding the VLM so pipeline workers stay light and the
serving backend is swappable without touching pipeline code.

```
POST /vlm/generate  { image_b64, prompt, max_tokens, json_schema? } → { text, backend, model, usage }
GET  /healthz       → { status, backend, model, device, loaded, configured_backend }
```

| Backend (`CDI_MLSERVE_BACKEND`) | Use |
|---|---|
| `hf` | transformers `AutoModelForImageTextToText` = Qwen2.5-VL-7B, bf16, `attn_implementation="sdpa"`, `max_pixels` capped (2 MP) to bound VRAM; **lazy-load on first request**, 7B→3B fallback on OOM |
| `stub` | deterministic keyword responder — CI / no-GPU |

Client side (`cdi_adapter.ml.client`):
- `HttpMLClient` / `StubMLClient` chosen by env.
- `vlm_json(image, prompt, schema)` = generate → `extract_json` (tolerates fences /
  prose) → **`repair_payload`** (rehomes values put in the wrong key, fills required
  confidence, drops stray keys, resolves `cdi:common.defs` `$ref`) → validate
  against a `referencing.Registry` of all local schemas → on repeated failure
  returns the repaired best-effort object with `_partial: true` **instead of
  raising**. A document is never lost to a formatting nit.

### 3.5 Runtime processes & ports

| Process | Port | Exposure | Notes |
|---|---|---|---|
| `cdi_adapter.webapp` | 8888 | **public** via `https://<pod-id>-8888.proxy.runpod.net` | 8888 is the only RunPod-proxied HTTP port on this pod (was Jupyter; Jupyter stopped) |
| `cdi_adapter.mlserve` | 8077 | localhost only | GPU model gateway |
| PostgreSQL | 5432 | localhost | overlay `PGDATA`, `-k /tmp` |
| Redis | 6379 | localhost | Celery broker (folder-watch path) |
| MinIO | 9000 / 9001 | localhost | bucket `cdi-documents` |

---

## 4. Data model (as built)

PostgreSQL 16, `jsonb` throughout. DDL: [`db/schema.sql`](../db/schema.sql);
read-model views: [`db/views.sql`](../db/views.sql); migration:
[`db/alembic/versions/0001_initial_schema.py`](../db/alembic/versions/0001_initial_schema.py).
20 tables in 13 groups; DAL is raw SQL in
[`src/cdi_adapter/repo.py`](../src/cdi_adapter/repo.py) (no ORM drift).

### 4.1 Table groups

| Group | Tables | Purpose |
|---|---|---|
| Ingestion evidence (immutable) | `source_document`, `document_page`, `pipeline_run` | the scan, its normalized pages, every stage run with model name/version + metrics |
| Classification | `doc_classification` | doc_type, specialty, languages, handwritten, page spans, confidence |
| OCR evidence | `ocr_block` | line/word/cell text + `bbox int[]` + `ocr_conf` + reading order + table refs; GIN full-text index |
| Raw extraction | `extraction` | schema-locked VLM payload (`jsonb`) + `evidence_map` (block-id map), pre-normalization, auditable |
| Identity (MPI) | `patient_identity`, `patient_identity_alias` | one durable identity; ABHA / legacy MRN; every name spelling seen |
| Encounters | `encounter` | derived per document; class AMB/IMP; date precision (year/month/day) |
| **Clinical facts (EAV core)** | `clinical_fact`, `medication_detail` | see §4.2 |
| **Provenance** | `fact_provenance` | see §4.3 |
| Conflicts | `fact_conflict` | 3-valued evidence state (`SUPPORTED` / `CONTRADICTED` / `UNKNOWN_NOT_MENTIONED`) — table exists, engine not built |
| FHIR projection | `fhir_resource`, `fhir_bundle` | see §4.4 |
| Human review | `review_task` | queue — not yet populated |
| ABDM | `abdm_care_context`, `abdm_consent`, `abdm_transfer` | linking / consent / encrypted push — schema only |
| Audit | `audit_log` | every create/update/read with actor + detail |
| Read model (views) | `v_problem_list`, `v_medication_list`, `v_allergy_list`, `v_results_grid`, `v_encounter_timeline` | flattened lists clinicians read |

### 4.2 The EAV clinical-fact core

`clinical_fact` is an OpenMRS-`obs` / Cerner-`CLINICAL_EVENT` lineage: one row per
atomic assertion, only the relevant value column filled.

```
clinical_fact
  patient_id, encounter_id
  fact_type        condition | symptom | finding | lab_result | vital_sign |
                   procedure | medication | allergy | immunization |
                   diagnostic_report | advice | care_plan | referral | document
  code_system, code, code_display, code_status   -- unmapped | candidate | bound | local_only
  local_text                                     -- verbatim clinical phrase
  value_kind  value_num  value_unit_ucum         -- quantity
  value_code_system/code/display                 -- codeable value
  value_text  value_bool  value_low/high_num
  ref_range_low/high/text  abnormal_flag         -- lab context
  clinical_status  verification  onset  effective_time  asserted_time
  extraction_id  source_doc_ids[]
  confidence_overall / _ocr / _extract / _terminology   -- fused
  review_state     pending | auto_accepted | in_review | clinician_confirmed | corrected | rejected
  supersedes  is_current  dedup_key

medication_detail (1:1 with a medication fact)
  drug_text, rxlike_system/code, form, strength_num/unit,
  dose_num/dose_unit_ucum, route, frequency_code, frequency_per_day,
  duration_days, prn, instructions, intent (order | record)
```

### 4.3 Provenance (first-class)

Every `clinical_fact` gets ≥1 `fact_provenance` row:

```
fact_provenance
  fact_id → clinical_fact
  source_doc_id, page_id
  ocr_block_ids uuid[]          -- the exact OCR spans that support the fact
  bbox_union   int[]            -- highlight rectangle for a reviewer UI
  extracted_text               -- the substring the value came from
  pipeline_run_ids uuid[]       -- classify+ocr+extract chain
  model_stack  jsonb            -- {classifier, ocr, extractor, terminology, projector} names+versions
  agent        'system' | <clinician id>
  recorded_at
```

Enables: clinician review with pixel highlight, medico-legal traceability, model
evaluation, and reprocessing when a better model ships.

### 4.4 FHIR projection tables

```
fhir_resource   (resource_type, fhir_id, version_id) UNIQUE
  patient_id, encounter_id, profile text[], resource jsonb,
  derived_from_facts uuid[], validation_status (pending|valid|warning|error), validation_issues jsonb

fhir_bundle
  patient_id, encounter_id, care_context,
  artifact_type   PrescriptionRecord | DiagnosticReportRecord | OPConsultRecord |
                  DischargeSummaryRecord | WellnessRecord | HealthDocumentRecord | ...
  bundle jsonb    -- Bundle(type=document): Composition + all referenced resources + Provenance
  bundle_hash char(64), fhir_version '4.0.1', ig_package 'nrces.fhir.r4.ndhm#6.5.0',
  validation_status, status (draft|validated|ready_to_share|shared|superseded)
```

---

## 5. Pipeline implementation — stage by stage

### S1 — Ingest & normalize · `cdi_adapter.ingest`

`ingest_bytes(raw, filename, mime_type, source_channel, legacy_ref, legacy_patient_ref, …)`:

1. SHA-256 → if a `source_document` with that hash exists, **return it (idempotent
   dedupe)** and audit a read.
2. Store original bytes → `s3://cdi-documents/documents/<sha[:2]>/<sha>/original.<ext>`.
3. Insert `source_document` (status `received`), audit `create`.
4. Open `pipeline_run(stage='ingest')`.
5. `render_pages(raw, mime)` — PyMuPDF for PDF (one PNG/page at `CDI_PAGE_DPI`=200),
   Pillow for images, multi-frame TIFF supported.
6. `normalize_image(png)` per page: grayscale → **deskew** (`cv2.minAreaRect` on
   Otsu-thresholded ink pixels; rotate if `0.15° < |skew| ≤ 15°`) → `fastNlMeansDenoising`
   → CLAHE. `preproc` jsonb records `skew_deg` + `steps`. Original bytes are never
   altered — these are derived render artifacts.
7. Store `pages/NNNN.png` + `pages/NNNN.thumb.png`; insert `document_page`.
8. `status='pages_rendered'`, finish run, audit, enqueue S2 (Celery path) / return.

Failure → `pipeline_run` `failed`, `source_document.status='error'`, error detail
persisted, exception re-raised.

### S2 — Classify · `cdi_adapter.classify`

- `_ocr_hint(page1)` — a fast RapidOCR pass on the full first-page image → top ~25
  lines of text, wrapped in `<<<OCR_SAMPLE>>>…<<<END_OCR_SAMPLE>>>`. Grounds the VLM
  and makes the stub deterministic. Best-effort; `None` if the engine is absent.
- `build_classification_prompt(hint, n_pages)` → instruction listing the 11
  `doc_type` values + rules for `specialty` / `is_handwritten` / `languages` /
  `page_spans` / `confidence`.
- `client.vlm_json(page1_image, prompt, classification.v1)` → validated dict.
- Insert `doc_classification`, finish `pipeline_run(stage='classify')`,
  `status='classified'`, audit, enqueue S3.
- Observed: 9/9 synthetic docs correct at confidence 0.95; ~15 s/call (plus one-off
  ~35 s model load).

### S3 — OCR / layout · `cdi_adapter.ocr`

- Route: `is_handwritten AND CDI_HANDWRITTEN_USES_VLM` → **VLM transcription**
  (`vlm_ocr.run_vlm_transcription`, one line per output line, page-level bbox,
  conf 0.55); else **RapidOCR** (`rapid.run_rapidocr`).
- RapidOCR (`rapidocr-onnxruntime`, CPU): returns `[quad, text, score]`; kept if
  `score ≥ CDI_OCR_MIN_CONF` (0.30); quad → `bbox [x0,y0,x1,y1]`; `polygon`
  retained.
- `order_reading()` — sort by horizontal band (`round(y_center / (0.7·median_height))`)
  then `x0` → natural top-to-bottom, left-to-right within a line.
- One `pipeline_run(stage='ocr')`; `delete_ocr_blocks_for_document` first
  (idempotent re-run); bulk `insert_ocr_blocks`; `status='ocr_done'`; audit; enqueue S4.
- Observed: printed lab report → 23–39 blocks, line confidences 0.94–1.00.

### Identity — MPI, fetched from the documents · `cdi_adapter.mpi`

The uploader supplies **only the files** (ABHA optional). Identity is read from the
documents themselves:

- `candidate_from_payload` pulls `name` / `sex` / `age_text` / `dob` from each
  document's extracted `patient` block; `parse_name` strips titles
  (`Dr`/`Mr`/`S/o`/…), `parse_sex` normalises to `M`/`F`/`O`, `parse_age` →
  approximate birth year.
- `resolve_identity` (called by S4 for the first document of a job):
  1. **ABHA** match → existing patient;
  2. else **fuzzy**: `SequenceMatcher` on the normalised name + birth-year within 1
     → existing patient at ratio ≥ 0.90;
  3. else **mint** a new record with `mpi_id = CFP-<YYYY>-<seq>` (Postgres
     `patient_mpi_seq`).
- Every document's raw reading is stored in `patient_identity_alias`.
- After all of a job's documents: `merge_identity_evidence` picks the
  most-supported name / sex / DOB (mode, longest on ties) and sets
  `identity_confidence` from how strongly the documents agreed.
- The FHIR `Patient` carries the CareFlow id as an identifier
  (`system: https://careflow.clinic/mpi`, `use: usual`) alongside ABHA, plus
  `gender` and `birthDate`.

Verified: 3 sample documents, no manual entry → `CFP-2026-000001`, name "Anjali
Das", sex M, DOB 1978-01-01, `identity_confidence 0.99`.

### S4 — Extract · `cdi_adapter.extract`

- Schema by `doc_type` (`extract/prompt.SCHEMA_FOR_DOC_TYPE`): `prescription.v3`,
  `lab_report.v3`, `vitals.v3`, `opd_note.v3` (also referral), `discharge_summary.v3`
  (also operative_note), `radiology.v3`. Unknown type → skip, `status='normalized'`.
- `build_extraction_prompt(doc_type, ocr_blocks)` presents blocks as `[b1] text …`,
  `[b2] …`; `block_id_map` maps `bN → ocr_block uuid`.
- `client.vlm_json(page1_image, prompt, schema, max_tokens=1600)` — with repair +
  `_partial` fallback (see §3.4). Payload stored verbatim in `extraction`.
- **Identity & encounter**: `patient_id` is passed in by the web-app job (created
  once from the form); otherwise `get_or_create_patient` from the payload's
  `patient` block / legacy MRN. One `encounter` per document, class `IMP` for
  discharge/operative else `AMB`, dated from `encounter_date` / `reported_at` /
  `study_date` / `discharge_date` / `recorded_at` (partial-date aware).
- **Payload → facts**: per-`doc_type` handlers (`_facts_prescription`, `_facts_lab`,
  `_facts_vitals`, `_facts_discharge`, `_facts_radiology`, `_facts_opd_note`) using
  **tolerant readers**:
  - `_qty(x)` accepts a `{value,unit,evidence}` object, a bare number, or a string
    like `"500 mg"` / `"138/86"`.
  - `_coded_text(x)` accepts `{text|code|display|name}` or a plain string.
  - `_parse_date(s)` handles `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, `DD-Mon-YYYY`, `DD/MM/YYYY`.
  - Vitals are de-duplicated within a document and only emitted when a numeric value
    was actually read (`_VITAL_ALIAS` normalizes pulse→heart_rate, etc.).
- Each fact: `insert_clinical_fact` (+ `insert_medication_detail`), then
  `insert_fact_provenance` resolving the evidence `bN` ids to `ocr_block` uuids and
  computing `bbox_union`. Fused `confidence_overall = 0.5·(extract_conf + mean OCR conf)`.
- `pipeline_run(stage='extract')` ok with `facts` count; `status='extracted'`; audit;
  enqueue S5.
- Observed (real 7B): prescription → 9 facts, lab report → 5 facts, vitals → 1–8.
  `_partial` warnings are common — the 7B often mis-slots values — but the data is
  recovered by the tolerant readers.

### S5 — Terminology · `cdi_adapter.terminology`

- `seed.py`: hand-curated maps — ~35 conditions, ~10 symptoms, ~4 procedure
  families, ~30 lab analytes → LOINC, ~14 vital signs → LOINC, ~15 drugs → SNOMED,
  ~6 allergens → SNOMED, plus `FREQ_PER_DAY`, `ROUTE_SCT`, `UCUM_ALIASES`.
- `bind_fact(fact)`: normalize `local_text` (lowercase, fix `hba1c`, strip
  punctuation) → exact hit → alias → `difflib.get_close_matches(cutoff=0.86)`.
  Medications: strip `tab/cap/syrup/…` then match any token. Sets
  `code_system/code/code_display`, `code_status = bound | local_only`,
  `confidence_terminology = 0.9 | 0.4`. Units → UCUM; `frequency_per_day` parsed
  from `1-0-1` / `BD` / `TDS` / …
- `bind_document(document_id)` updates every fact from that document; `status='normalized'`.
- Observed: labs 5/5 → LOINC; meds 3/3 → SNOMED; allergy → SNOMED; conditions
  bound when in the seed map, else `local_only` (text preserved).

### S6 — Clinical validation + the governance gate · `cdi_adapter.validate`

**No LLM.** `rules.py` holds deterministic checks; each returns
`(severity ∈ {blocker, warn, info}, code, message)`:

| Rule | What it checks |
|---|---|
| `check_value_range` | lab/vital value against an absolute physiological window per LOINC (`LAB_RANGE` / `VITAL_RANGE`); auto-normalizes temperature °F→°C; flags unit mismatch |
| `check_unit_present` | numeric lab/vital has a unit |
| `check_medication` | dose/strength present, frequency present, `0 < freq/day ≤ 6`, total-daily-dose ≤ 1.5× an adult ceiling (`DOSE_MAX_MG_DAY` per SNOMED substance) |
| `check_dates` | `effective_time`/`onset`/`asserted_time` not in the future, not < 1900, within ±400 d of the encounter |
| `check_evidence` | fact has a `fact_provenance` row with ≥1 OCR block id **or** extracted text |
| `check_terminology` | condition/medication/allergy/procedure is `bound` (else a `warn`) |

`service.validate_document(document_id)`:
1. Reads the fact set + the extraction row → `partial = payload._partial`.
2. Per fact: run all rules; `_calibrate(conf, partial, n_warn, n_block)` —
   an honest placeholder: subtract `CDI_GATE_PARTIAL_PENALTY` (0.30) when the
   extraction was `_partial`, cap at 0.5 on any blocker, 0.9 on any warn.
   *(roadmap: fitted isotonic regression per `doc_type × fact_type`.)*
3. **Gate → `review_state`:**
   | Condition | Result |
   |---|---|
   | any `blocker` **or** `_partial` **or** `conf < CDI_GATE_REVIEW_FLOOR` (0.85) **or** medication with a `med-*` finding | `in_review` |
   | `conf ≥ CDI_GATE_AUTO_ACCEPT_CONF` (0.985) **and** zero findings | `auto_accepted` |
   | `conf ≥ CDI_GATE_AUDIT_CONF` (0.95) **and** zero blockers | `auto_accepted` (+ `audit_sample_rate` → a low-priority audit `review_task`) |
   | otherwise | `in_review` |
4. Cross-document: `find_similar_current_facts` (same patient, `fact_type`, `code`)
   → same-day different value ⇒ `fact_conflict(value_mismatch, CONTRADICTED,
   blocker)` and both facts forced `in_review`; same-day same value ⇒
   `fact_conflict(duplicate, SUPPORTED, info, auto_resolution=keep_a)`.
5. One `review_task` per document listing every held fact id, `kind` inferred
   (`dose_check` / `unmapped_terminology` / `low_confidence`), `payload` carries
   the structured findings.
6. `pipeline_run(stage='validate')` with counts; `status='validated'`; audit.

Observed: a `_partial` prescription extraction → **0 auto-accepted, all facts
`in_review`** — exactly the intended behaviour ("malformed/uncertain extraction
never silently becomes trusted clinical data").

### S8 — Human adjudication · `cdi_adapter.webapp.review`

- `GET /review` — HTML queue (`review_page.py`): open `review_task`s grouped by
  document, each held fact shown with confidence + proposed code.
- `GET /api/review/facts/{fact_id}` (`review.fact_detail`) assembles: the fact, its
  `medication_detail`, every `fact_provenance` row → resolved `ocr_block` texts +
  the `bbox_union` + the page image URL (`/api/documents/{id}/pages/{n}`) + the
  rule findings.
- The page renders the **page image with the evidence bbox drawn over it**, the OCR
  span, the extracted value, the proposed code, the confidence, the findings, and
  three actions:
  - **accept** → `review_state='clinician_confirmed'`
  - **correct** → `apply_fact_correction` (whitelisted fields: text, code(+system),
    value/unit, status, ranges) → `review_state='corrected'`, `supersedes` chain
  - **reject** → `review_state='rejected'`, `is_current=false`,
    `clinical_status='entered-in-error'`
- Every decision writes a `fact_provenance` row with `agent = <reviewer>` and
  `model_stack = {"reviewer": "human"}`, and closes the `review_task` once none of
  its facts remain `in_review`. Then `GET /api/jobs/{id}/fhir` **re-projects live**,
  so the bundle now asserts the newly-governed facts.

### S9 — FHIR projection (gated) · `cdi_adapter.fhir`

- `resources.py` — pure dict builders, references as `urn:uuid:` full-urls (ABDM
  document-bundle style). Profiles asserted in `meta.profile` against
  `https://nrces.in/ndhm/fhir/r4/StructureDefinition/…`.
- `ARTIFACT` map: `doc_type → (profile, Composition.type SNOMED coding, title)`:
  prescription→`PrescriptionRecord`, lab/radiology→`DiagnosticReportRecord`,
  opd_note/referral→`OPConsultRecord`, discharge/operative→`DischargeSummaryRecord`,
  vitals→`WellnessRecord`, else `HealthDocumentRecord`.
- `project_document(document_id)`:
  1. Load facts + med details + patient + encounter; base64 the original scan
     (≤ 12 MB) for the `DocumentReference.content.attachment.data`.
  2. Emit core actors: `Patient` (ABHA identifier `https://healthid.ndhm.gov.in`),
     `Organization` (HFR), `Practitioner` (HPR), `Encounter`.
  3. `fact_type → resource`:
     | fact_type | resource | binding |
     |---|---|---|
     | condition | `Condition` | SNOMED CT + clinical/verification status |
     | symptom | `Observation` (exam) | SNOMED CT |
     | finding | `Observation` (exam) | — |
     | lab_result | `Observation` (laboratory) | LOINC + UCUM `valueQuantity`, `referenceRange`, `interpretation` |
     | vital_sign | `Observation` (vital-signs) | LOINC + UCUM |
     | medication | `MedicationRequest` | SNOMED CT drug, `dosageInstruction` (dose, timing `frequency/period`, route), `dispenseRequest` duration |
     | procedure | `Procedure` | SNOMED CT, `performedDateTime`, note = findings |
     | allergy | `AllergyIntolerance` | SNOMED CT substance + reaction manifestation |
     | diagnostic_report | `DiagnosticReport` | LOINC/SNOMED, `result[]` → the lab Observations, `conclusion` |
  4. `DocumentReference` (the scan) + `Provenance` (`target` = every emitted
     clinical resource + the DocumentReference; `entity` = source; `agent` =
     assembler with the model stack string).
  5. `Composition` with sections (Chief complaints / Diagnosis / Investigations /
     Medications / Procedures / Allergies / Vital signs / Document reference /
     Advisory notes) → `Bundle(type=document)`.
  6. `_lint(bundle)` — structural checks: first entry is a Composition; **no
     dangling `urn:uuid:` references**; Observations have a value; Conditions coded.
     Errors/warnings recorded on every `fhir_resource` + `fhir_bundle` row.
  7. Persist `fhir_resource` rows + one `fhir_bundle` (`validation_status`,
     `status='validated'` when clean); `pipeline_run(stage='project')`;
     `source_document.status='projected'`; audit.
- `project_patient(patient_id)` — every document for that patient → its bundle;
  returns `{patient, artifact_count, bundles:[{filename, doc_type, artifact_type,
  issues, bundle}]}`.
- Observed last clean run: 3 bundles, **0 structural error issues**, meds and labs
  fully coded.

---

## 6. FHIR / ABDM mapping notes

- **Bundle** = `type: "document"`, `identifier` `urn:cdi:bundle`, `timestamp`, first
  `entry` is the `Composition`, all references are `urn:uuid:`.
- **Composition.type** uses SNOMED CT (e.g. Prescription record `440545006`,
  Diagnostic studies report `721981007`, Discharge summary `373942005`).
- **Patient.identifier** carries the ABHA number (`https://healthid.ndhm.gov.in`)
  and, when present, ABHA address and the legacy MRN (`urn:cdi:legacy-mrn`).
- **The gate is enforced here:** `project_document` splits facts into `governed`
  (`review_state ∈ {auto_accepted, clinician_confirmed, corrected}`) and `held`.
  Only governed facts become FHIR resources and Composition entries. Held facts are
  returned in `held_facts` and added as `info` issues; the bundle is
  `status='draft'` (`validation_status='warning'`) until they are all adjudicated,
  then `status='ready_to_share'` (`'valid'`). Structural errors → `status='draft'`,
  `'error'`.
- **Provenance** is always included — the record is never worse than "the scan,
  shared digitally"; even a fully-held extraction yields a valid, `draft`
  `HealthDocumentRecord` carrying the image.
- **FHIR content validation today = structural lint only** (`_lint`: Composition
  first, no dangling `urn:uuid:`, Observations valued, Conditions coded). Full HAPI
  + `nrces.fhir.r4.ndhm` IG profile validation is a named gap (§13).

---

## 7. Web application (`cdi_adapter.webapp`)

Branded **CareFlow Polyclinic** — white + blue, inline-SVG logo, system fonts, no
build step / no CDN. `theme.py` holds the palette tokens, type scale, spacing,
component styles and the shared header; `page.py` (upload + results) and
`review_page.py` (S8 console) render from it. The upload form has **no name/sex
fields** — a drag-and-drop dropzone, an optional ABHA input, and a note that
identity is read from the documents. After processing, a **patient banner** shows
the generated `CFP-…` id, name, sex, DOB and identity confidence.

### 7.1 Endpoints

| Method / path | Purpose |
|---|---|
| `GET /` | single-page upload UI (`page.py`, dark theme, no build step) |
| `GET /healthz` | `{db, s3, mlserve}` checks |
| `POST /api/jobs` | multipart: `abha?` + `files[]` (≤10) → `{job_id}` (202). No name/sex — read from the documents. |
| `GET /api/jobs/{id}` | job + `patient` (`mpi_id`, name, sex, DOB, identity_confidence) + per-document `{status, step, doc_type, facts, accepted, in_review}` |
| `GET /api/jobs/{id}/fhir` | `project_patient` result (all bundles) |
| `GET /api/jobs/{id}/fhir/download` | same as a downloadable `fhir_<job>.json` |
| `GET /api/patients/{id}/fhir` | bundles for an existing patient |
| `GET /api/documents/{id}/bundle` | single-document bundle (non-persisting) |
| `GET /api/documents/{id}/evidence` | doc_type + OCR block count + facts table |
| `GET /api/documents/{id}/pages/{n}` | the normalized page PNG (used by the review UI) |
| `GET /review` | S8 human review console (page) |
| `GET /api/review/tasks` | open `review_task`s with their held facts |
| `GET /api/review/facts/{fact_id}` | fact + provenance + OCR blocks + bbox + page image url + findings |
| `POST /api/facts/{fact_id}/review` | `{action: accept\|correct\|reject, corrections?, reviewer?, note?}` |

`GET /api/jobs/{id}/fhir` re-runs `project_patient` **live** each call, so it always
reflects the latest review decisions.

### 7.2 Job model (`webapp/jobs.py`)

- `ThreadPoolExecutor(max_workers=1)` — the GPU/gateway is single, so pipeline runs
  are serialized. Job state is an in-memory `Job`/`DocProg` dataclass; the durable
  record is the DB rows.
- `_run_job`: create/lookup patient from the form → for each file
  `ingest_bytes → classify_document → ocr_document → extract_document(patient_id=…)
  → bind_document`; a per-document failure is captured on that `DocProg` and the
  job continues → finally `project_patient(pid)` → `job.result`.
- The page polls `GET /api/jobs/{id}` every 1.5 s and renders progress; on
  completion it fetches `/fhir` and renders, per bundle: a facts table
  (Resource / Concept / Code system+code / Value), a "Copy this bundle" button, and
  the pretty-printed JSON; plus "Download all JSON".

### 7.3 Timing (RTX PRO 4500, 7B bf16)

| step | first doc | subsequent |
|---|---|---|
| model load | ~35 s (once) | — |
| classify (VLM) | ~15 s | ~15 s |
| OCR (RapidOCR) | ~7 s | ~7 s |
| extract (VLM, up to 3 tries) | ~40–50 s | ~40–50 s |
| terminology + FHIR | < 1 s | < 1 s |
| **per document** | ~110 s | ~65–75 s |

5 documents ≈ 5–7 minutes. The UI shows live progress throughout.

---

## 8. End-to-end flow

### 8.1 Sequence (web-app path)

```
User        webapp            jobs(thread)     mlserve(VLM)   RapidOCR    Postgres/MinIO
 │  POST /api/jobs (5 files)   │                │              │           │
 │ ───────────────────────────▶│ create patient ───────────────────────────▶ patient_identity
 │  202 {job_id}               │                │              │           │
 │                             │ for each file: │              │           │
 │                             │  ingest ───────────────────────────────────▶ source_document, pages→MinIO
 │                             │  classify ────▶ /vlm/generate │            │
 │                             │        ◀──────  doc_type       │            │
 │                             │  ─────────────────────────────▶ hint OCR    │
 │                             │  ocr ──────────────────────────▶ blocks     │
 │                             │  ─────────────────────────────────────────▶ ocr_block
 │                             │  extract ─────▶ /vlm/generate (schema)      │
 │                             │        ◀──────  JSON (repair/_partial)      │
 │                             │  ─────────────────────────────────────────▶ extraction, clinical_fact, fact_provenance
 │                             │  terminology (seed map, in-proc) ─────────▶ update clinical_fact.code_*
 │  GET /api/jobs/{id} (poll)  │                │              │           │
 │ ◀───────────────────────────│ per-doc status │              │           │
 │                             │ project_patient ─────────────────────────▶ fhir_resource, fhir_bundle
 │  GET /api/jobs/{id}/fhir    │                │              │           │
 │ ◀───────────────────────────│ bundles JSON   │              │           │
```

### 8.2 Worked example — "Anjali Das", 3 documents (last verified run)

| Upload | S2 | S4 facts | S5 | S9 artifact | Result |
|---|---|---|---|---|---|
| `prescription_00.pdf` | `prescription` 0.95 | 9 (2 conditions, 3 meds, BP, weight) | T2DM→SCT 44054006, HTN→59621000; Metformin→372567009, Amlodipine→386864001, Atorvastatin→373444002; BP→LOINC 8480-6/8462-4; weight→29463-7 | `PrescriptionRecord`, 15 resources | **0 errors** |
| `lab_report_01.pdf` | `lab_report` 0.95 | 5 lab results | HbA1c→LOINC 4548-4 (7.8 %), FBS→1558-6 (142 mg/dL), creatinine→2160-0, cholesterol→2093-3, LDL→2089-1 | `DiagnosticReportRecord`, 12 resources | **0 errors** |
| `vitals_sheet_02.pdf` | `vitals_sheet` 0.95 | 1 (Penicillin allergy) | Penicillin→SCT 373270004 | `WellnessRecord`, 8 resources | **0 errors** |

Output: `GET /api/jobs/{id}/fhir` → 3 `Bundle(type=document)`; each downloadable;
15 `clinical_fact` rows + 15 `fact_provenance` rows + 35 `fhir_resource` rows in
Postgres.

---

## 9. Configuration (`cdi_adapter.config.Settings`, env prefix `CDI_`)

| Key | Default (pod) | Meaning |
|---|---|---|
| `CDI_DATABASE_URL` | `postgresql+psycopg://cdi:cdi@127.0.0.1:5432/cdi` | adapter DB |
| `CDI_REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker |
| `CDI_S3_ENDPOINT_URL` / `_ACCESS_KEY` / `_SECRET_KEY` / `_BUCKET` | MinIO on `127.0.0.1:9000`, `cdi-documents` | object store |
| `CDI_INBOX_DIR` / `_PROCESSED_DIR` / `_FAILED_DIR` | `/workspace/data/*` | folder-watch |
| `CDI_PAGE_DPI` | 200 | render resolution |
| `CDI_WEBAPP_PORT` | **8888** | the RunPod-proxied port |
| `CDI_MLSERVE_URL` / `_PORT` | `http://127.0.0.1:8077` / 8077 | model gateway |
| `CDI_MLSERVE_BACKEND` | `hf` | `hf` \| `stub` |
| `CDI_VLM_MODEL_ID` / `_FALLBACK_MODEL_ID` | `Qwen/Qwen2.5-VL-7B-Instruct` / `…-3B-…` | VLM + OOM fallback |
| `CDI_VLM_MAX_PIXELS_OCR` | 2 000 000 | processor cap (VRAM bound) |
| `CDI_OCR_ENGINE` / `CDI_HANDWRITTEN_USES_VLM` | `rapidocr` / `true` | S3 routing |
| `CDI_IG_PACKAGE` | `nrces.fhir.r4.ndhm#6.5.0` | Profile/IG package |
| `CDI_TERMINOLOGY_PACKAGE` | `in-snomed-loinc-icd10` | Terminology package |

Pod file: `/workspace/cdi/.env` (copied from `.env.runpod`).

---

## 10. Operations / runbook

### 10.1 First boot on a fresh pod
```bash
# sync the repo to /workspace/cdi  (scp a tarball or git clone), then:
cd /workspace/cdi && cp .env.runpod .env
bash infra/runpod/start_all.sh
# → prints:  LIVE URL: https://<POD_ID>-8888.proxy.runpod.net
```

### 10.2 After every restart (nothing lost)
```bash
bash /workspace/cdi/infra/runpod/start_all.sh
```
`bootstrap_pod.sh` reinstalls apt bits, re-inits the Postgres cluster on the
overlay, **`pg_restore`s `/workspace/backup/cdi.dump`**, runs `alembic upgrade
head`, rebuilds the venv if missing; then the gateway and web app start. Model
weights are already in `/workspace/hf-cache`; scans are already in
`/workspace/minio-data`.

### 10.3 DB snapshots
`snapshot.sh` (`pg_dump -Fc` → `/workspace/backup/cdi.dump`, keeps last 10) is on
`cron` every 15 min; run it manually before a planned pod stop.

### 10.4 Logs & health
```
/workspace/logs/mlserve.log     model load, every /vlm/generate
/workspace/logs/webapp.log      pipeline events (classified, ocr_done, extracted,
                                terminology_bound, projected, vlm_json_partial, job_doc_failed)
/workspace/logs/bootstrap.log   infra bring-up
curl -s localhost:8888/healthz  {db,s3,mlserve}
curl -s localhost:8077/healthz  {model, loaded, device}
psql postgresql://cdi:cdi@127.0.0.1:5432/cdi -f scripts/verify_s3.sql
```
Common issues: SSH sessions drop >~10 s (use `setsid nohup … </dev/null &`);
RunPod nginx returns HTTP 200 with a fake 502 page for unbound ports (health
checks must grep a content marker); `psql`/`pg_restore` need the URL with
`+psycopg` stripped.

---

## 11. Testing

`pytest` (`tests/`): ~32 unit + 2 integration.
- Unit (no infra): page render/deskew/thumbnail, mime sniffing, `extract_json`,
  classification schema + prompt, `StubMLClient`, OCR reading-order, terminology
  helpers (`freq_per_day`, `to_ucum`), `repair_payload` + `validate_schema` for all
  extraction schemas, **S6 rules** (value ranges, °F normalization, medication
  dose/frequency/ceiling, future/implausible dates, evidence-present) and the
  `_calibrate` gate function.
- Integration (`-m integration`, needs Postgres+MinIO, stub VLM): full
  `ingest → classify → ocr` producing `doc_classification` + `ocr_block` rows with
  bboxes and `pipeline_run` stages `ok`.
- `scripts/pipeline_smoke.py` drives S1–S3 (or S1–S9 via the web app) over the
  generated sample docs and prints a summary; `scripts/make_sample_docs.py`
  generates rotated/noisy synthetic prescriptions / lab reports / vitals sheets.

---

## 12. Security & compliance posture (current)

| Area | Now | Gap |
|---|---|---|
| Data residency | all inference + PHI on the pod; no external API calls in the hot path | — |
| Legacy DB | strictly read-only; never touched | — |
| Web UI auth | **none** | add auth before any real data |
| Transport | RunPod proxy provides TLS to the browser; internal is plaintext localhost | mTLS for a multi-node deployment |
| At rest | MinIO + Postgres on the pod; DB dump on `/workspace` | encryption at rest, KMS |
| Audit | `audit_log` on every create/update/read | ship to a WORM store |
| De-identification | none (demo data) | Presidio + clinical NER before any training corpus |
| ABDM | schema for `abdm_care_context/consent/transfer`; **no gateway, no Fidelius** | build the DMZ edge (§14) |
| DPDP Act 2023 | not addressed | consent, purpose limitation, retention, data-principal rights |
| Secret hygiene | note: `RUNPOD_API_KEY` is readable in `/proc/1/environ` on this pod | rotate; don't share pods |

---

## 13. What is built vs. not — production gap analysis

| Capability | State | Notes |
|---|---|---|
| S1 ingest + normalize | ✅ built, tested | dedupe, deskew/denoise, page store |
| S2 classify (real VLM) | ✅ built, 9/9 on samples | Qwen2.5-VL-7B |
| S3 OCR (printed + handwriting) | ✅ built | RapidOCR + VLM; printed strong, handwriting untested at scale |
| S4 extract (schema-locked) | ⚠️ built, **quality-limited** | 7B mis-slots JSON often → repair marks `_partial`; **the S6 gate now holds all `_partial` facts for review** rather than trusting them. Medication dose parsing and vitals extraction are still the weak spots — fixed properly by the fine-tuned DSLM |
| S5 terminology | ⚠️ **seed map only** | ~120 concepts; no Snowstorm, no SapBERT/FAISS, no ICD, no ConceptMap `$translate` |
| **S6 clinical validation + gate** | ✅ **built** | deterministic rules (ranges, units, dose ceilings, dates, evidence, unmapped) + calibration placeholder + routing; `fact_conflict` populated (duplicate + same-day contradiction); 3-valued `evidence_state` recorded |
| S7 longitudinal reconciliation | ❌ not built | temporal merge, `supersedes` chains beyond corrections, derived summary graph |
| **S8 human review console** | ✅ **built** | `/review`: image + bbox + OCR + fact + code + confidence + findings → accept/correct/reject; reviewer-signed provenance; live re-projection. Not yet: auth, assignment/SLA queue, throughput tooling |
| S9 FHIR projection (gated) | ✅ built | asserts only governed facts; `ready_to_share` vs `draft`; 0 structural errors on last clean run |
| FHIR/IG validation | ⚠️ **structural lint only** | no HAPI validator, no `nrces.fhir.r4.ndhm` package check, no terminology `$validate-code` |
| Fine-tuned DSLM (`cdi-dslm`, Llama/Qwen-7B + LoRA) | ❌ not built | **the biggest gap** — Qwen2.5-VL currently does extraction; no QLoRA training, no eval harness, no structured decoding (XGrammar) |
| Identity / MPI | ⚠️ minimal | form-driven `get_or_create_patient`; no blocking/scoring, no ABHA verification |
| ABDM HIP/HRP gateway + consent + Fidelius | ❌ not built | schema only |
| Legacy write-back / FHIR façade | ❌ not built | |
| Ingestion at scale | ⚠️ | web app is a single-thread `ThreadPool`; Celery chain exists but unused here; no queue backpressure, no retries tuning |
| Observability | ⚠️ | structlog to files; no Prometheus/Grafana/OTel |
| HA / DR | ❌ | single pod; DB dump is the only backup |
| AuthN/AuthZ, rate limiting, DPDP workflow | ❌ | |

**Honest summary:** a functional, on-prem, open-source **prototype** that takes real
scanned documents to ABDM-shaped FHIR — not a production system.

---

## 14. Roadmap to production (ordered)

The governance chain the reviewer asked for is now in place —
`scan → OCR → extraction → schema validation → clinical validation → confidence
calibration → human review when required → FHIR` — but several links are
placeholder-grade:

| # | Work | Status |
|---|---|---|
| 1 | **Harden S4** — fine-tune `cdi-dslm` (Qwen2.5-7B / Llama-3.1-8B + QLoRA) on synthetic + de-identified gold; serve via vLLM + **XGrammar** grammar-locked decoding so JSON is valid by construction; eval harness (field F1, numeric exactness, hallucination, FHIR validity). Removes the `_partial` path. | **next** |
| 2 | **Real terminology service** — Snowstorm-lite (SNOMED CT India) + LOINC/ICD in Postgres; SapBERT/BioLORD + FAISS candidate gen + rule reranker; `$validate-code` / `$translate`; local-code minting. Replaces `seed.py`. | next |
| 3 | **Fit the S6 calibrator** — replace `_calibrate` with isotonic regression per `doc_type × fact_type` on clinician-adjudicated data; derive the gate thresholds empirically per class. | after data |
| 4 | **S7 longitudinal reconciliation** — temporal merge across encounters, `supersedes` chains, medication continuity, derived patient-summary view. | — |
| 5 | **FHIR/IG validation in-loop** — HAPI validator + `nrces.fhir.r4.ndhm` package + terminology `$validate-code`; bundle fails on `error`, quarantines on IG `warning`. | — |
| 6 | **Review console hardening** — auth, reviewer assignment + SLA queue, keyboard-driven throughput, correction diffs as training data, dual-review for high-risk facts. | — |
| 7 | **ABDM edge** — DMZ service: HFR/HPR registration, care-context linking, consent-artifact intake, Fidelius (ECDH) encryption, HIU push + status callback; keys never persisted. | — |
| 8 | **Scale** — durable queue (Temporal / tuned Celery), separate ingest/OCR/VLM worker pools, GPU batching, priority + dead-letter queues, idempotency keys, autoscaling. Replaces the single `ThreadPool`. | — |
| 9 | **Ops & governance** — Prometheus/Grafana/Loki + OTel; drift monitors (confidence dist, human-override rate, unmapped-code rate); model registry + canary/rollback; Postgres primary+standby; MinIO 3-node; KMS; web-app AuthN/AuthZ; DPDP data-principal workflow; WORM audit. | — |
| 10 | **Shadow-mode pilot** on real historical documents with clinician adjudication before anything is trusted or shared. | — |

### The gate, as implemented

```
model output ──▶ schema validation ──▶ repair ──▶ (still invalid?) mark _partial
                                                       │
clinical_fact + terminology binding + provenance ──────┤
                                                       ▼
                                          S6 deterministic rules
                                    (range · unit · dose · date · evidence ·
                                     terminology · duplicate · contradiction)
                                                       │
                                            confidence calibration
                                                       │
                            ┌──────────────────────────┴───────────────────────────┐
                            ▼                                                       ▼
          conf ≥ 0.985 & no findings                         blocker | _partial | conf < 0.85 |
          conf ≥ 0.95 & no blockers (+ audit sample)         med-* finding | same-day contradiction
                            │                                                       │
                            ▼                                                       ▼
                     review_state =                                         review_state =
                     auto_accepted                                          in_review  (+ review_task)
                            │                                                       │
                            │                                          S8 console: accept / correct / reject
                            │                                                       │
                            └──────────────┬────────────────────────────────────────┘
                                           ▼
                       S9 FHIR projection asserts ONLY governed facts
                       bundle.status = ready_to_share  (all governed, lint-clean)
                                       | draft         (held facts | structural error)
```

---

## Appendix A — Repo layout

```
clinical-emr-adapter/
├── docs/
│   ├── DESIGN.md            north-star design + 10-country research
│   ├── ARCHITECTURE.md      this document (as-built)
│   └── adr/0001-adapter-not-replacement.md
├── db/
│   ├── schema.sql  views.sql  apply.sh
│   └── alembic/…/0001_initial_schema.py
├── schemas/                 classification.v1, common.defs, {prescription,lab_report,vitals,
│                            opd_note,discharge_summary,radiology}.v3
├── src/cdi_adapter/
│   ├── config.py db.py storage.py repo.py logging.py
│   ├── ingest/   pages.py service.py watcher.py
│   ├── classify/ prompt.py service.py
│   ├── ocr/      rapid.py vlm_ocr.py service.py
│   ├── extract/  prompt.py service.py
│   ├── terminology/ seed.py service.py
│   ├── validate/ rules.py service.py            (S6 clinical validation + gate)
│   ├── fhir/     resources.py service.py
│   ├── ml/       client.py            (HttpMLClient, StubMLClient, repair_payload, registry)
│   ├── mlserve/  app.py backends.py __main__.py   (model gateway)
│   ├── webapp/   app.py jobs.py page.py review.py review_page.py __main__.py
│   ├── worker.py  api.py
├── infra/
│   ├── runpod/   start_all.sh  bootstrap_pod.sh  start_mlserve.sh  snapshot.sh  README.md
│   └── compose/  docker-compose.yml Dockerfile      (not used on the no-Docker pod)
├── scripts/      make_sample_docs.py  pipeline_smoke.py  ingest_batch.py  verify_s3.sql
└── tests/        test_pages / test_ingest_unit / test_ml_and_classify_unit /
                  test_ocr_order_unit / test_ingest_integration / test_pipeline_integration
```

## Appendix B — Model & dependency stack (as installed on the pod)

| Component | Version / build |
|---|---|
| GPU | NVIDIA RTX PRO 4500 Blackwell, 32 GB |
| CUDA / driver | 13.0 / 580 |
| Python | 3.12 |
| torch / torchvision | 2.8.0+cu128 / 0.23.0+cu128 (inherited via `venv --system-site-packages`) |
| transformers | 5.16.1 |
| VLM | `Qwen/Qwen2.5-VL-7B-Instruct` (Apache-2.0), bf16, ~16 GB VRAM, sdpa attention |
| OCR | `rapidocr-onnxruntime` + `onnxruntime` (CPU) |
| API / server | FastAPI + uvicorn |
| DB / store / broker | PostgreSQL 16, MinIO (RELEASE.2025-09-07), Redis 7 |
| schema validation | `jsonschema` + `referencing` registry |
| DB access | SQLAlchemy 2 (Core `text()`), psycopg 3 |

## Appendix C — Live endpoints (current pod `2kkk36y0r2z7yv`)

```
UI            https://2kkk36y0r2z7yv-8888.proxy.runpod.net/
review        https://2kkk36y0r2z7yv-8888.proxy.runpod.net/review
health        …/healthz
submit        POST …/api/jobs              (multipart: patient_name, abha?, gender?, files[])
poll          GET  …/api/jobs/{id}         (per-doc facts / accepted / in_review)
bundles       GET  …/api/jobs/{id}/fhir    (re-projected live; ready_to_share vs draft)
download      GET  …/api/jobs/{id}/fhir/download
review queue  GET  …/api/review/tasks
fact detail   GET  …/api/review/facts/{fact_id}
decision      POST …/api/facts/{fact_id}/review   {action, corrections?, reviewer?}
```

## Appendix D — Verified S6/S8 run (2026-09-08)

3 documents, patient "Anjali Das". The 7B extraction returned `_partial` for all
three → **the gate held every fact**: `auto_accepted = 0`, `in_review = 15`
(prescription 9, lab 5, vitals 1), 3 `review_task`s, all bundles `status = draft`,
`asserted_facts = 0`. Accepting one lab fact via
`POST /api/facts/{id}/review {action:"accept","reviewer":"dr.sen"}` →
`GET /api/jobs/{id}/fhir` re-projected with `lab_report … asserted=1 held=4`.
`fact_detail` returned the page-image URL, the `bbox_union`
`[426,1041,2488,1107]`, the OCR blocks (`"HbAlc" 0.937`, `"4.0-5.6" 0.995`) and
the rule findings — everything the review UI overlays on the scan.
