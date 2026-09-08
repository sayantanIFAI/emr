-- CDI-Adapter canonical schema (PostgreSQL 16).
-- Authoritative DDL. The Alembic migration 0001 executes this verbatim.
-- The legacy HMS database is NEVER touched; this is the adapter's own store.
-- No explicit BEGIN/COMMIT: Alembic wraps this in its own transaction, and
-- `db/apply.sh` runs it with `psql -1 -v ON_ERROR_STOP=1`.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid on older PG; PG16 has it in core too

-- ========== 1. INGESTION & SOURCE EVIDENCE (immutable) ==========

CREATE TABLE IF NOT EXISTS source_document (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_ref         text,
  legacy_patient_ref text,
  original_filename  text,
  sha256             char(64) NOT NULL UNIQUE,
  mime_type          text NOT NULL,
  byte_size          bigint NOT NULL DEFAULT 0,
  page_count         int  NOT NULL DEFAULT 0,
  object_uri         text NOT NULL,
  captured_at        timestamptz,
  ingested_at        timestamptz NOT NULL DEFAULT now(),
  source_channel     text NOT NULL,                     -- legacy_cdc|folder_watch|batch|manual|api
  status             text NOT NULL DEFAULT 'received',  -- received|pages_rendered|classified|extracted|projected|error
  error_detail       text
);

CREATE TABLE IF NOT EXISTS document_page (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id    uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  page_no        int  NOT NULL,
  width_px       int, height_px int, dpi int,
  image_uri      text NOT NULL,
  thumb_uri      text,
  preproc        jsonb NOT NULL DEFAULT '{}',
  UNIQUE (document_id, page_no)
);

CREATE TABLE IF NOT EXISTS pipeline_run (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  stage         text NOT NULL,     -- ingest|classify|ocr|extract|normalize|validate|reconcile|project|deliver
  status        text NOT NULL,     -- ok|retry|failed|skipped|running
  model_name    text, model_version text, model_sha text,
  params        jsonb NOT NULL DEFAULT '{}',
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz,
  metrics       jsonb NOT NULL DEFAULT '{}',
  input_sha     text, output_sha text,
  error_detail  text
);
CREATE INDEX IF NOT EXISTS ix_pipeline_run_doc ON pipeline_run (document_id, stage);

-- ========== 2. CLASSIFICATION ==========

CREATE TABLE IF NOT EXISTS doc_classification (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  doc_type      text NOT NULL,
  specialty     text,
  language      text[] NOT NULL DEFAULT '{}',
  is_handwritten bool NOT NULL DEFAULT false,
  page_spans    jsonb NOT NULL DEFAULT '[]',
  confidence    numeric(4,3) NOT NULL,
  model_run_id  uuid REFERENCES pipeline_run(id),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_doc_classification_doc ON doc_classification (document_id);

-- ========== 3. OCR / LAYOUT EVIDENCE ==========

CREATE TABLE IF NOT EXISTS ocr_block (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id       uuid NOT NULL REFERENCES document_page(id) ON DELETE CASCADE,
  block_type    text NOT NULL,     -- line|word|cell|kv_key|kv_value|table|title|signature|stamp
  reading_order int,
  text          text NOT NULL,
  bbox          int[]  NOT NULL,   -- [x0,y0,x1,y1] page px
  polygon       jsonb,
  ocr_conf      numeric(4,3) NOT NULL,
  lang          text,
  table_ref     uuid,
  row_idx int, col_idx int,
  model_run_id  uuid REFERENCES pipeline_run(id)
);
CREATE INDEX IF NOT EXISTS ix_ocr_block_page ON ocr_block (page_id);
CREATE INDEX IF NOT EXISTS ix_ocr_block_fts  ON ocr_block USING gin (to_tsvector('simple', text));

-- ========== 4. RAW MODEL EXTRACTION (pre-normalization, auditable) ==========

CREATE TABLE IF NOT EXISTS extraction (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id    uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  schema_name    text NOT NULL,
  schema_version text NOT NULL,
  payload        jsonb NOT NULL,
  evidence_map   jsonb NOT NULL DEFAULT '{}',
  model_run_id   uuid REFERENCES pipeline_run(id),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_extraction_doc ON extraction (document_id);

-- ========== 5. PATIENT IDENTITY (adapter-side MPI) ==========

CREATE TABLE IF NOT EXISTS patient_identity (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_mrn    text,
  abha_number   text, abha_address text,
  name_given    text, name_family text,
  gender        text, birth_date date, birth_date_est bool NOT NULL DEFAULT false,
  phone_hash    text, address_hash text,
  match_status  text NOT NULL DEFAULT 'unlinked',   -- unlinked|auto|clerk_confirmed|abha_verified
  match_score   numeric(4,3),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_patient_mrn  ON patient_identity (legacy_mrn);
CREATE INDEX IF NOT EXISTS ix_patient_abha ON patient_identity (abha_number);

CREATE TABLE IF NOT EXISTS patient_identity_alias (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id   uuid REFERENCES patient_identity(id) ON DELETE SET NULL,
  document_id  uuid REFERENCES source_document(id) ON DELETE CASCADE,
  raw_name text, raw_mrn text, raw_dob text, raw_age text, raw_phone text,
  ocr_block_ids uuid[] NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- ========== 6. ENCOUNTERS (derived) ==========

CREATE TABLE IF NOT EXISTS encounter (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  class         text NOT NULL,               -- AMB|IMP|EMER|VR
  period_start  timestamptz, period_end timestamptz,
  period_precision text NOT NULL DEFAULT 'day',
  facility_ref  text, practitioner_ref text, specialty text,
  derived_from  uuid[] NOT NULL DEFAULT '{}',
  confidence    numeric(4,3) NOT NULL DEFAULT 0,
  review_state  text NOT NULL DEFAULT 'pending',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_encounter_patient ON encounter (patient_id, period_start);

-- ========== 7. CLINICAL FACTS (EAV core: "rows in a table") ==========

CREATE TABLE IF NOT EXISTS clinical_fact (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id      uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  encounter_id    uuid REFERENCES encounter(id) ON DELETE SET NULL,
  fact_type       text NOT NULL,   -- condition|symptom|finding|lab_result|vital_sign|procedure|
                                   -- medication|allergy|immunization|diagnostic_report|advice|care_plan|referral|document
  code_system     text,
  code            text,
  code_display    text,
  code_status     text NOT NULL DEFAULT 'unmapped',  -- unmapped|candidate|bound|local_only
  local_text      text NOT NULL,
  value_kind      text,            -- quantity|codeable|string|boolean|datetime|range|ratio
  value_num       numeric,
  value_unit_ucum text,
  value_code_system text, value_code text, value_code_display text,
  value_text      text,
  value_bool      bool,
  value_low_num numeric, value_high_num numeric,
  ref_range_low numeric, ref_range_high numeric, ref_range_text text,
  abnormal_flag   text,
  clinical_status text,
  verification    text,
  onset           timestamptz, onset_precision text,
  effective_time  timestamptz, effective_precision text,
  asserted_time   timestamptz,
  extraction_id   uuid REFERENCES extraction(id) ON DELETE SET NULL,
  source_doc_ids  uuid[] NOT NULL DEFAULT '{}',
  confidence_overall     numeric(4,3) NOT NULL DEFAULT 0,
  confidence_ocr         numeric(4,3),
  confidence_extract     numeric(4,3),
  confidence_terminology numeric(4,3),
  review_state    text NOT NULL DEFAULT 'pending',   -- pending|auto_accepted|in_review|clinician_confirmed|corrected|rejected
  reviewed_by     text, reviewed_at timestamptz, review_note text,
  supersedes      uuid REFERENCES clinical_fact(id) ON DELETE SET NULL,
  is_current      bool NOT NULL DEFAULT true,
  dedup_key       text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fact_patient  ON clinical_fact (patient_id, fact_type, is_current);
CREATE INDEX IF NOT EXISTS ix_fact_dedup    ON clinical_fact (dedup_key);
CREATE INDEX IF NOT EXISTS ix_fact_pending  ON clinical_fact (review_state) WHERE review_state = 'pending';

CREATE TABLE IF NOT EXISTS medication_detail (
  fact_id        uuid PRIMARY KEY REFERENCES clinical_fact(id) ON DELETE CASCADE,
  drug_text      text NOT NULL,
  rxlike_system  text, rxlike_code text,
  form           text, strength_num numeric, strength_unit text,
  dose_num       numeric, dose_unit_ucum text,
  route          text, frequency_code text,
  frequency_per_day numeric, duration_days int,
  prn            bool, instructions text,
  intent         text NOT NULL DEFAULT 'order'   -- order|record
);

-- ========== 8. PROVENANCE (first-class) ==========

CREATE TABLE IF NOT EXISTS fact_provenance (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fact_id       uuid NOT NULL REFERENCES clinical_fact(id) ON DELETE CASCADE,
  source_doc_id uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  page_id       uuid REFERENCES document_page(id) ON DELETE SET NULL,
  ocr_block_ids uuid[] NOT NULL DEFAULT '{}',
  bbox_union    int[],
  extracted_text text NOT NULL,
  pipeline_run_ids uuid[] NOT NULL DEFAULT '{}',
  model_stack   jsonb NOT NULL DEFAULT '{}',
  prompt_sha    text,
  agent         text NOT NULL DEFAULT 'system',
  recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_prov_fact ON fact_provenance (fact_id);

-- ========== 9. CONFLICTS & RECONCILIATION ==========

CREATE TABLE IF NOT EXISTS fact_conflict (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  fact_a        uuid NOT NULL REFERENCES clinical_fact(id) ON DELETE CASCADE,
  fact_b        uuid REFERENCES clinical_fact(id) ON DELETE CASCADE,
  conflict_type text NOT NULL,
  evidence_state text NOT NULL,   -- SUPPORTED|CONTRADICTED|UNKNOWN_NOT_MENTIONED
  severity      text NOT NULL,    -- info|warn|blocker
  auto_resolution text,
  resolved_by   text, resolved_at timestamptz, resolution_note text,
  status        text NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS ix_conflict_patient ON fact_conflict (patient_id, status);

-- ========== 10. FHIR PROJECTION ("and in json") ==========

CREATE TABLE IF NOT EXISTS fhir_resource (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid REFERENCES patient_identity(id) ON DELETE CASCADE,
  encounter_id  uuid REFERENCES encounter(id) ON DELETE SET NULL,
  resource_type text NOT NULL,
  fhir_id       text NOT NULL,
  version_id    int  NOT NULL DEFAULT 1,
  profile       text[] NOT NULL DEFAULT '{}',
  resource      jsonb NOT NULL,
  derived_from_facts uuid[] NOT NULL DEFAULT '{}',
  validation_status text NOT NULL DEFAULT 'pending',   -- pending|valid|warning|error
  validation_issues jsonb NOT NULL DEFAULT '[]',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (resource_type, fhir_id, version_id)
);
CREATE INDEX IF NOT EXISTS ix_fhir_resource_patient ON fhir_resource (patient_id, resource_type);

CREATE TABLE IF NOT EXISTS fhir_bundle (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  encounter_id  uuid REFERENCES encounter(id) ON DELETE SET NULL,
  care_context  text,
  artifact_type text NOT NULL,   -- OPConsultRecord|PrescriptionRecord|DiagnosticReportRecord|
                                 -- DischargeSummaryRecord|WellnessRecord|ImmunizationRecord|HealthDocumentRecord
  bundle        jsonb NOT NULL,
  bundle_hash   char(64) NOT NULL,
  validation_status text NOT NULL DEFAULT 'pending',
  fhir_version  text NOT NULL DEFAULT '4.0.1',
  ig_package    text NOT NULL DEFAULT 'nrces.fhir.r4.ndhm#6.5.0',
  status        text NOT NULL DEFAULT 'draft',   -- draft|validated|ready_to_share|shared|superseded
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fhir_bundle_patient ON fhir_bundle (patient_id, artifact_type, status);

-- ========== 11. HUMAN-IN-THE-LOOP REVIEW ==========

CREATE TABLE IF NOT EXISTS review_task (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid REFERENCES patient_identity(id) ON DELETE CASCADE,
  document_id   uuid REFERENCES source_document(id) ON DELETE CASCADE,
  kind          text NOT NULL,   -- low_confidence|conflict|identity_match|dose_check|unmapped_terminology|schema_reject|handwriting
  ref_fact_ids  uuid[] NOT NULL DEFAULT '{}',
  ref_conflict_id uuid REFERENCES fact_conflict(id) ON DELETE SET NULL,
  priority      int NOT NULL DEFAULT 3,
  sla_due       timestamptz,
  assignee      text,
  status        text NOT NULL DEFAULT 'queued',   -- queued|in_progress|done|escalated
  payload       jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  closed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS ix_review_open ON review_task (status, priority) WHERE status IN ('queued','in_progress');

-- ========== 12. ABDM LINKAGE / CONSENT / TRANSFER ==========

CREATE TABLE IF NOT EXISTS abdm_care_context (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id uuid NOT NULL REFERENCES encounter(id) ON DELETE CASCADE,
  patient_id uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  care_context_ref text NOT NULL UNIQUE,
  hi_types text[] NOT NULL DEFAULT '{}',
  display  text NOT NULL,
  linked_at timestamptz,
  link_status text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS abdm_consent (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  consent_artifact_id text NOT NULL,
  patient_id uuid NOT NULL REFERENCES patient_identity(id) ON DELETE CASCADE,
  hi_types text[] NOT NULL DEFAULT '{}',
  date_from date, date_to date,
  expiry timestamptz, purpose_code text,
  artifact jsonb NOT NULL DEFAULT '{}',     -- metadata only; encryption keypair is NEVER persisted
  received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS abdm_transfer (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  consent_id uuid REFERENCES abdm_consent(id) ON DELETE SET NULL,
  bundle_id uuid NOT NULL REFERENCES fhir_bundle(id) ON DELETE CASCADE,
  transaction_id text, hiu_id text,
  status text NOT NULL DEFAULT 'prepared',  -- prepared|encrypted|pushed|acknowledged|failed
  pushed_at timestamptz, ack_at timestamptz, error text
);

-- ========== 13. AUDIT ==========

CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL,
  entity text NOT NULL, entity_id text,
  patient_id uuid,
  detail jsonb NOT NULL DEFAULT '{}',
  ip inet, request_id text
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log (entity, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log (ts);
