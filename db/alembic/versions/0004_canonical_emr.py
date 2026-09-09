"""canonical longitudinal EMR (Layer 1 clinical + Layer 2 ops/finance) +
AI provenance layer + workbasket/worklist review layer.

FHIR R4 / ABDM are an interoperability layer generated from these tables - they
are NOT this schema. Nothing here is written by the extraction pipeline directly:
rows land only after a human approves a review item (see promote/service.py).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
-- =====================================================================
-- LAYER 1  -  CLINICAL TRUTH  (cn_*)
-- =====================================================================

-- 03 Practitioner ------------------------------------------------------
CREATE TABLE IF NOT EXISTS cn_practitioner (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hpr_id              text,
  registration_number text,
  name_prefix         text,
  name_given          text,
  name_family         text,
  name_full           text,
  qualification       text[],
  specialty           text[],
  department          text,
  organization_id     uuid,
  phone               text,
  email               text,
  license_status      text DEFAULT 'active',
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cn_pract_hpr ON cn_practitioner (hpr_id) WHERE hpr_id IS NOT NULL;

-- 04 Organization ----------------------------------------------------
CREATE TABLE IF NOT EXISTS cn_organization (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                text NOT NULL,
  type                text,                       -- prov | dept | team | ...
  registration_number text,                       -- HFR / OIN
  accreditation       text,                       -- NABH / NABL / ...
  phone               text,
  email               text,
  address_text        text,
  parent_id           uuid REFERENCES cn_organization(id),
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- 05 Location --------------------------------------------------------
CREATE TABLE IF NOT EXISTS cn_location (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id  uuid REFERENCES cn_organization(id),
  name             text NOT NULL,
  kind             text,                          -- building|floor|department|ward|room|bed
  parent_id        uuid REFERENCES cn_location(id),
  status           text DEFAULT 'active',
  geo              jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- 01 Patient master  (UUID pk; ABHA is NEVER the key) ---------------
CREATE TABLE IF NOT EXISTS cn_patient (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mpi_id                text,                      -- link to legacy patient_identity.mpi_id (CFP-...)
  enterprise_patient_id text,                      -- EMPI
  name_prefix           text,
  name_given            text,
  name_middle           text,
  name_family           text,
  name_full             text,
  date_of_birth         date,
  birth_date_precision  text,                      -- year|month|day
  gender                text,                      -- M|F|O|U  (administrative)
  sex_at_birth          text,
  marital_status        text,
  blood_group           text,
  preferred_language    text,
  communication_pref    text,
  deceased              boolean NOT NULL DEFAULT false,
  deceased_datetime     timestamptz,
  privacy_flags         jsonb NOT NULL DEFAULT '{}'::jsonb,
  status                text NOT NULL DEFAULT 'active',   -- active|inactive|merged
  merged_into           uuid REFERENCES cn_patient(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_patient_mpi ON cn_patient (mpi_id);
CREATE INDEX IF NOT EXISTS ix_cn_patient_name ON cn_patient (lower(name_full));

-- 02 External identifiers (ABHA / MRN / insurance / local ...) ------
CREATE TABLE IF NOT EXISTS cn_patient_identifier (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id   uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  system       text NOT NULL,        -- ABHA | ABHA_ADDRESS | MRN | AADHAAR_VID | INSURANCE | OIN | LOCAL
  value        text NOT NULL,
  use          text DEFAULT 'official',
  assigner     text,
  period_start date,
  period_end   date,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cn_pid_sys_val ON cn_patient_identifier (patient_id, system, value);
CREATE INDEX IF NOT EXISTS ix_cn_pid_abha ON cn_patient_identifier (value) WHERE system IN ('ABHA','ABHA_ADDRESS');

CREATE TABLE IF NOT EXISTS cn_patient_contact (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id  uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  kind        text NOT NULL,        -- mobile | phone | email
  value       text NOT NULL,
  rank        int  NOT NULL DEFAULT 1,
  preferred   boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS cn_patient_address (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id  uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  use         text DEFAULT 'home',
  line        text[],
  city        text,
  district    text,
  state       text,
  postal_code text,
  country     text DEFAULT 'IN'
);

CREATE TABLE IF NOT EXISTS cn_patient_related_person (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id   uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  relationship text,                 -- emergency_contact | guardian | spouse | ...
  name_full    text,
  phone        text
);

-- 06 Encounter  (the central clinical anchor) ----------------------
CREATE TABLE IF NOT EXISTS cn_encounter (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id             uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_number       text,
  klass                  text NOT NULL DEFAULT 'OPD',   -- OPD|IPD|ER|ICU|DAYCARE|TELEMEDICINE|DIAGNOSTIC
  status                 text NOT NULL DEFAULT 'finished',
  priority               text,
  service_type           text,
  specialty              text,
  department             text,
  attending_practitioner uuid REFERENCES cn_practitioner(id),
  organization_id        uuid REFERENCES cn_organization(id),
  location_id            uuid REFERENCES cn_location(id),
  appointment_id         uuid,
  reason_text            text,
  chief_complaint_text   text,
  period_start           timestamptz,
  period_end             timestamptz,
  admission_source       text,
  discharge_disposition  text,
  coverage_id            uuid,
  derived_from_documents uuid[] NOT NULL DEFAULT '{}',
  created_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_enc_patient ON cn_encounter (patient_id, period_start);

CREATE TABLE IF NOT EXISTS cn_encounter_participant (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id   uuid NOT NULL REFERENCES cn_encounter(id) ON DELETE CASCADE,
  practitioner_id uuid REFERENCES cn_practitioner(id),
  role           text NOT NULL DEFAULT 'consulting'   -- attending|consulting|care_team|referrer
);

-- 08 Clinical note (structured SOAP) ------------------------------
CREATE TABLE IF NOT EXISTS cn_clinical_note (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id  uuid REFERENCES cn_encounter(id) ON DELETE CASCADE,
  author_id     uuid REFERENCES cn_practitioner(id),
  author_role   text,
  note_type     text,
  subjective    jsonb NOT NULL DEFAULT '{}'::jsonb,   -- chief_complaint, hpi, past/surgical/family/social/medication history
  objective     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- vitals, physical_examination, clinical_observations
  assessment    jsonb NOT NULL DEFAULT '{}'::jsonb,   -- diagnosis[], differential[], risk[]
  plan          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- medication_orders[], investigations[], procedures[], referrals[], follow_up
  free_text     text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  signed_at     timestamptz,
  signed_by     uuid REFERENCES cn_practitioner(id),
  version       int NOT NULL DEFAULT 1
);

-- 09/10 Chief complaint (presenting - NOT a Condition) -----------
CREATE TABLE IF NOT EXISTS cn_chief_complaint (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id  uuid REFERENCES cn_encounter(id) ON DELETE CASCADE,
  text          text NOT NULL,
  duration_text text,
  onset_date    date,
  recorded_at   timestamptz NOT NULL DEFAULT now()
);

-- 14 Condition / Problem  (problem list vs encounter diagnosis) --
CREATE TABLE IF NOT EXISTS cn_condition (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id        uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  category            text NOT NULL DEFAULT 'ENCOUNTER_DIAGNOSIS',   -- PROBLEM|ENCOUNTER_DIAGNOSIS|HEALTH_CONCERN|OTHER
  code_system         text,                       -- http://snomed.info/sct | http://hl7.org/fhir/sid/icd-10 | local
  code                text,
  display             text NOT NULL,
  clinical_status     text DEFAULT 'active',       -- active|recurrence|relapse|inactive|remission|resolved
  verification_status text DEFAULT 'provisional',  -- unconfirmed|provisional|differential|confirmed|refuted
  severity            text,
  onset_date          date,
  recorded_date       date,
  resolved_date       date,
  recorder_id         uuid REFERENCES cn_practitioner(id),
  asserter            text,
  note                text,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_cond_patient ON cn_condition (patient_id, category);

-- 15 Allergy / intolerance  (high-safety) ----------------------
CREATE TABLE IF NOT EXISTS cn_allergy (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id        uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  substance_system    text,
  substance_code      text,
  substance_display   text NOT NULL,
  category            text,                        -- medication|food|environment|biologic
  criticality         text,                        -- low|high|unable-to-assess
  clinical_status     text DEFAULT 'active',
  verification_status text DEFAULT 'unconfirmed',
  reaction_manifestation text,
  reaction_severity   text,
  onset               date,
  recorded_by         text,
  recorded_at         timestamptz NOT NULL DEFAULT now()
);

-- 12/13 Observation (vitals, labs, exam, imaging measures) -----
CREATE TABLE IF NOT EXISTS cn_observation (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id         uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  category             text NOT NULL DEFAULT 'vital-sign',   -- vital-sign|laboratory|exam|social-history|imaging|other
  code_system          text,                        -- http://loinc.org | http://snomed.info/sct | local
  code                 text,
  display              text NOT NULL,
  value_num            numeric,
  value_unit_ucum      text,
  value_string         text,
  value_code_system    text,
  value_code           text,
  ref_range_low        numeric,
  ref_range_high       numeric,
  ref_range_text       text,
  interpretation       text,                        -- N|H|L|HH|LL|A
  effective_time       timestamptz,
  issued_time          timestamptz,
  performer            text,
  device_id            uuid,
  method               text,
  body_site            text,
  specimen_id          uuid,
  derived_from_lab_result uuid,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_obs_patient ON cn_observation (patient_id, category, effective_time);

-- 16 Medication master --------------------------------------------
CREATE TABLE IF NOT EXISTS cn_medication (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  generic_name   text,
  brand_name     text,
  strength_num   numeric,
  strength_unit  text,
  dosage_form    text,
  route_default  text,
  manufacturer   text,
  code_system    text,
  code           text,
  formulary      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_cn_med_name ON cn_medication (lower(coalesce(brand_name, generic_name)));

-- 17 Medication order (prescribed) ------------------------------
CREATE TABLE IF NOT EXISTS cn_medication_order (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id         uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  medication_id        uuid REFERENCES cn_medication(id),
  drug_text            text NOT NULL,
  status               text NOT NULL DEFAULT 'active',   -- active|on-hold|completed|stopped|draft
  intent               text NOT NULL DEFAULT 'order',    -- proposal|plan|order
  dose_num             numeric,
  dose_unit_ucum       text,
  route                text,
  frequency_code       text,
  frequency_per_day    numeric,
  timing_text          text,
  duration_days        int,
  quantity             numeric,
  refills              int,
  start_date           date,
  end_date             date,
  prescriber_id        uuid REFERENCES cn_practitioner(id),
  reason_condition_id  uuid REFERENCES cn_condition(id),
  instructions         text,
  substitution_allowed boolean,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_medord_patient ON cn_medication_order (patient_id);

-- 18 Medication administration (given - inpatient) -------------
CREATE TABLE IF NOT EXISTS cn_medication_administration (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id         uuid REFERENCES cn_medication_order(id) ON DELETE CASCADE,
  patient_id       uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  administered_by  text,
  scheduled_time   timestamptz,
  actual_time      timestamptz,
  dose_given_num   numeric,
  dose_unit_ucum   text,
  route            text,
  status           text NOT NULL DEFAULT 'completed',   -- completed|not-done|on-hold|stopped
  reason_not_given text
);

-- 19 Medication dispense (pharmacy) ---------------------------
CREATE TABLE IF NOT EXISTS cn_medication_dispense (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id       uuid REFERENCES cn_medication_order(id) ON DELETE CASCADE,
  patient_id     uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  pharmacy       text,
  quantity       numeric,
  days_supply    int,
  handed_over_at timestamptz,
  status         text NOT NULL DEFAULT 'completed'
);

-- 20-23 Lab chain: order -> specimen -> result -> diagnostic report
CREATE TABLE IF NOT EXISTS cn_lab_order (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id        uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  ordered_by          uuid REFERENCES cn_practitioner(id),
  priority            text DEFAULT 'routine',
  requested_tests     jsonb NOT NULL DEFAULT '[]'::jsonb,
  clinical_indication text,
  status              text NOT NULL DEFAULT 'completed',
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cn_specimen (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id        uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  lab_order_id      uuid REFERENCES cn_lab_order(id) ON DELETE SET NULL,
  specimen_type     text,
  collection_time   timestamptz,
  collector         text,
  collection_site   text,
  accession_number  text,
  status            text DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS cn_lab_result (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  specimen_id      uuid REFERENCES cn_specimen(id) ON DELETE SET NULL,
  lab_order_id     uuid REFERENCES cn_lab_order(id) ON DELETE SET NULL,
  patient_id       uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id     uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  test_code_system text,
  test_code        text,
  test_name        text NOT NULL,
  value_num        numeric,
  value_unit_ucum  text,
  value_string     text,
  ref_range_low    numeric,
  ref_range_high   numeric,
  ref_range_text   text,
  abnormal_flag    text,
  interpretation   text,
  performed_time   timestamptz,
  verified_time    timestamptz,
  verified_by      text,
  instrument_id    text,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_labres_patient ON cn_lab_result (patient_id);

CREATE TABLE IF NOT EXISTS cn_diagnostic_report (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id           uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id         uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  lab_order_id         uuid REFERENCES cn_lab_order(id) ON DELETE SET NULL,
  category             text NOT NULL DEFAULT 'LAB',   -- LAB|RAD|PAT|CARD|MICRO|OTHER
  code_system          text,
  code                 text,
  display              text NOT NULL,
  status               text NOT NULL DEFAULT 'final',
  effective_time       timestamptz,
  issued_time          timestamptz,
  performer            text,
  interpreter          text,
  conclusion           text,
  result_observations  uuid[] NOT NULL DEFAULT '{}',
  specimens            uuid[] NOT NULL DEFAULT '{}',
  presented_form_doc   uuid,
  created_at           timestamptz NOT NULL DEFAULT now()
);

-- 24 Imaging study (metadata + references; PACS owns pixels) --
CREATE TABLE IF NOT EXISTS cn_imaging_study (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id            uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id          uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  accession_number      text,
  modality              text,
  body_site             text,
  started_time          timestamptz,
  performing_department text,
  dicom_study_uid       text,
  series_count          int,
  instance_count        int,
  radiologist           text,
  findings_text         text,
  impression_text       text,
  report_document_id    uuid,
  created_at            timestamptz NOT NULL DEFAULT now()
);

-- 25 Procedure -----------------------------------------------
CREATE TABLE IF NOT EXISTS cn_procedure (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id         uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id       uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  code_system        text,
  code               text,
  display            text NOT NULL,
  status             text NOT NULL DEFAULT 'completed',
  performed_time     timestamptz,
  performer          text,
  location_id        uuid REFERENCES cn_location(id),
  body_site          text,
  indication         text,
  outcome            text,
  complication       text,
  followup_text      text,
  report_document_id uuid,
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- 26/27 Care plan + goal -----------------------------------
CREATE TABLE IF NOT EXISTS cn_care_plan (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id  uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  status        text NOT NULL DEFAULT 'active',
  intent        text NOT NULL DEFAULT 'plan',
  title         text,
  description   text,
  period_start  date,
  period_end    date,
  author_id     uuid REFERENCES cn_practitioner(id),
  addresses     uuid[] NOT NULL DEFAULT '{}',   -- cn_condition ids
  activity      jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cn_goal (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_plan_id uuid REFERENCES cn_care_plan(id) ON DELETE CASCADE,
  patient_id   uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  description  text NOT NULL,
  target_measure text,
  target_value text,
  target_date  date,
  status       text DEFAULT 'active'
);

-- 28 Referral --------------------------------------------
CREATE TABLE IF NOT EXISTS cn_referral (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id            uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id          uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  status                text NOT NULL DEFAULT 'active',
  priority              text,
  referring_id          uuid REFERENCES cn_practitioner(id),
  referred_to_id        uuid REFERENCES cn_practitioner(id),
  referred_to_department text,
  reason_text           text,
  clinical_summary      text,
  requested_service     text,
  outcome               text,
  created_at            timestamptz NOT NULL DEFAULT now()
);

-- 35 Document layer (links the OCR/AI pipeline in) ------
CREATE TABLE IF NOT EXISTS cn_document (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id        uuid REFERENCES cn_encounter(id) ON DELETE SET NULL,
  document_type       text,
  title               text,
  author              text,
  created_time        timestamptz NOT NULL DEFAULT now(),
  service_time        timestamptz,
  mime_type           text,
  storage_uri         text,
  sha256              text,
  version             int NOT NULL DEFAULT 1,
  confidentiality     text DEFAULT 'N',
  ocr_text            text,
  source              text DEFAULT 'SCAN',        -- SCAN|VOICE|HL7|MANUAL
  source_document_id  uuid REFERENCES source_document(id) ON DELETE SET NULL
);

-- 37 Consent ------------------------------------------
CREATE TABLE IF NOT EXISTS cn_consent (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id  uuid NOT NULL REFERENCES cn_patient(id) ON DELETE CASCADE,
  purpose     text,
  scope       text,
  recipient   text,
  period_start timestamptz,
  period_end  timestamptz,
  status      text NOT NULL DEFAULT 'active',     -- draft|active|rejected|inactive|entered-in-error
  provision   jsonb NOT NULL DEFAULT '{}'::jsonb,
  granted_at  timestamptz,
  revoked_at  timestamptz
);

-- 38 Provenance (first-class on every promoted value) --
CREATE TABLE IF NOT EXISTS cn_provenance (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_table       text NOT NULL,
  target_id          uuid NOT NULL,
  patient_id         uuid,
  activity           text NOT NULL,      -- extract | human-review | promote | correction | merge
  agent_type         text NOT NULL,      -- ai | clinician | nurse | device | system
  agent_id           text,
  occurred_at        timestamptz,
  recorded_at        timestamptz NOT NULL DEFAULT now(),
  source_document_id uuid,
  ocr_span           text,
  bbox               int[],
  model_id           text,
  model_version      text,
  prompt_version     text,
  review_item_id     uuid,
  signature          text
);
CREATE INDEX IF NOT EXISTS ix_cn_prov_target ON cn_provenance (target_table, target_id);

-- 39 Audit event ------------------------------------
CREATE TABLE IF NOT EXISTS cn_audit_event (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_user    text,
  actor_role    text,
  patient_id    uuid,
  resource_table text,
  resource_id   uuid,
  action        text NOT NULL,           -- CREATE|READ|UPDATE|DELETE|EXPORT
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  source_system text,
  client_ip     text,
  reason        text
);

-- =====================================================================
-- LAYER 2  -  HOSPITAL OPERATIONS (ops_*)  &  FINANCIAL (fin_*)
-- skeletal: core columns + jsonb `data`; not fed by the document pipeline yet
-- =====================================================================
CREATE TABLE IF NOT EXISTS ops_appointment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  practitioner_id uuid, service_type text, status text DEFAULT 'booked',
  slot_start timestamptz, slot_end timestamptz, data jsonb NOT NULL DEFAULT '{}'::jsonb);

CREATE TABLE IF NOT EXISTS ops_ward (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid, name text NOT NULL, data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS ops_room (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), ward_id uuid REFERENCES ops_ward(id) ON DELETE CASCADE, name text, data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS ops_bed (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), room_id uuid REFERENCES ops_room(id) ON DELETE CASCADE, name text,
  status text NOT NULL DEFAULT 'AVAILABLE',   -- AVAILABLE|OCCUPIED|CLEANING|MAINTENANCE|RESERVED|BLOCKED
  patient_id uuid, data jsonb NOT NULL DEFAULT '{}'::jsonb);

CREATE TABLE IF NOT EXISTS ops_admission (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id uuid REFERENCES cn_encounter(id) ON DELETE SET NULL, admission_time timestamptz, admission_type text,
  admitting_doctor uuid, admitting_department text, bed_id uuid REFERENCES ops_bed(id),
  discharge_time timestamptz, data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS ops_transfer (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), admission_id uuid REFERENCES ops_admission(id) ON DELETE CASCADE,
  from_bed uuid, to_bed uuid, at_time timestamptz, reason text);

CREATE TABLE IF NOT EXISTS ops_nursing_assessment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id uuid REFERENCES cn_encounter(id) ON DELETE SET NULL, nurse text, assessed_at timestamptz,
  data jsonb NOT NULL DEFAULT '{}'::jsonb);   -- consciousness, pain, mobility, fall_risk, intake_output, ...
CREATE TABLE IF NOT EXISTS ops_nursing_event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id uuid, kind text, at_time timestamptz, data jsonb NOT NULL DEFAULT '{}'::jsonb);

CREATE TABLE IF NOT EXISTS ops_device (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), serial_number text, device_type text, manufacturer text, model text,
  department text, location_id uuid, status text DEFAULT 'active', data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS ops_device_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), device_id uuid REFERENCES ops_device(id) ON DELETE CASCADE,
  patient_id uuid, encounter_id uuid, ts timestamptz, parameter text, value_num numeric, unit text,
  quality text, alarm text);
CREATE TABLE IF NOT EXISTS ops_alert (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid, encounter_id uuid, device_id uuid,
  type text, severity text, status text DEFAULT 'active', raised_at timestamptz, ack_at timestamptz, resolved_at timestamptz);

CREATE TABLE IF NOT EXISTS fin_coverage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  payer text, policy_number text, member_id text, plan text, valid_from date, valid_to date,
  relationship text, coverage_type text, data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS fin_preauth (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), coverage_id uuid REFERENCES fin_coverage(id) ON DELETE CASCADE,
  encounter_id uuid, requested_amount numeric, approved_amount numeric, status text, data jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS fin_charge (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id uuid, service text, service_code text, quantity numeric, unit_price numeric, discount numeric,
  tax numeric, net_amount numeric, payer text);
CREATE TABLE IF NOT EXISTS fin_invoice (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid, encounter_id uuid, total numeric, status text DEFAULT 'issued',
  charges uuid[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS fin_payment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), invoice_id uuid REFERENCES fin_invoice(id) ON DELETE CASCADE,
  method text, amount numeric, kind text DEFAULT 'payment', at_time timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS fin_claim (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid, encounter_id uuid, payer text, preauth_id uuid,
  approved_amount numeric, rejected_amount numeric, status text DEFAULT 'submitted', data jsonb NOT NULL DEFAULT '{}'::jsonb);

-- =====================================================================
-- AI LAYER  (ai_*)  -  AI never writes cn_* directly; it writes here
-- =====================================================================
CREATE TABLE IF NOT EXISTS ai_event (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id          uuid,
  encounter_id        uuid,
  source_document_id  uuid REFERENCES source_document(id) ON DELETE SET NULL,
  source_audio_id     uuid,
  task                text NOT NULL,      -- transcription|entity_extraction|classification|prescription_ocr|diagnosis_suggestion|summarization|terminology_binding
  model_id            text,
  model_version       text,
  prompt_version      text,
  policy_version      text,
  input_ref           text,
  output_json         jsonb,
  confidence          numeric,
  human_review_status text NOT NULL DEFAULT 'pending',   -- pending|in_review|accepted|corrected|rejected|partial
  reviewed_by         text,
  reviewed_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_event_doc ON ai_event (source_document_id);

CREATE TABLE IF NOT EXISTS ai_suggestion (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ai_event_id    uuid REFERENCES ai_event(id) ON DELETE CASCADE,
  review_item_id uuid,
  staging_fact_id uuid,                 -- clinical_fact.id it came from
  target_kind    text NOT NULL,         -- condition|observation|medication_order|allergy|lab_result|diagnostic_report|procedure|imaging_study|document|encounter|patient_identifier
  proposed_json  jsonb NOT NULL,
  evidence       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- bbox, ocr_span, block ids
  confidence     numeric,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_clinical_verification (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ai_suggestion_id   uuid REFERENCES ai_suggestion(id) ON DELETE CASCADE,
  review_item_id     uuid,
  reviewer           text,
  decision           text NOT NULL,      -- accept|correct|reject
  corrected_json     jsonb,
  note               text,
  decided_at         timestamptz NOT NULL DEFAULT now(),
  final_resource_table text,
  final_resource_id  uuid
);

-- =====================================================================
-- REVIEW LAYER  (rv_*)  -  workbasket -> worklist -> approve
-- =====================================================================
CREATE TABLE IF NOT EXISTS rv_item (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind               text NOT NULL DEFAULT 'document',   -- document|encounter
  job_id             text,
  source_document_id uuid REFERENCES source_document(id) ON DELETE CASCADE,
  encounter_id       uuid,
  patient_identity_id uuid,               -- legacy patient_identity.id (pre-canonical)
  mpi_id             text,
  patient_display    text,
  doc_type           text,
  facility           text,
  priority           text NOT NULL DEFAULT 'routine',    -- routine|urgent|stat
  state              text NOT NULL DEFAULT 'open',        -- open|claimed|in_progress|approved|rejected|superseded
  assignee           text,
  n_elements         int NOT NULL DEFAULT 0,
  n_resolved         int NOT NULL DEFAULT 0,
  claimed_at         timestamptz,
  started_at         timestamptz,
  completed_at       timestamptz,
  sla_due_at         timestamptz,
  canonical_patient_id uuid,              -- filled on approve/promote
  bundle_id          uuid,
  bundle_status      text,
  validation         jsonb,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rv_item_state ON rv_item (state, priority, created_at);
CREATE INDEX IF NOT EXISTS ix_rv_item_assignee ON rv_item (assignee, state);

CREATE TABLE IF NOT EXISTS rv_item_element (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rv_item_id          uuid NOT NULL REFERENCES rv_item(id) ON DELETE CASCADE,
  fact_id             uuid,               -- clinical_fact.id
  fact_type           text,
  proposed_text       text,
  proposed_value_num  numeric,
  proposed_value_unit text,
  proposed_code_system text,
  proposed_code       text,
  proposed_freq_text  text,
  reviewer_text       text,
  reviewer_value_num  numeric,
  reviewer_value_unit text,
  decision            text NOT NULL DEFAULT 'pending',   -- pending|accept|correct|reject
  note                text,
  decided_at          timestamptz,
  bbox                int[],
  page_width          int,
  page_height         int
);
CREATE INDEX IF NOT EXISTS ix_rv_elem_item ON rv_item_element (rv_item_id);

CREATE TABLE IF NOT EXISTS rv_action (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rv_item_id uuid NOT NULL REFERENCES rv_item(id) ON DELETE CASCADE,
  actor      text,
  action     text NOT NULL,      -- create|claim|release|reassign|edit|approve|reject|reopen
  at_time    timestamptz NOT NULL DEFAULT now(),
  detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- FHIR artifacts generated from canonical rows (separate from the old fhir_bundle)
CREATE TABLE IF NOT EXISTS cn_fhir_bundle (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_patient_id uuid REFERENCES cn_patient(id) ON DELETE CASCADE,
  encounter_id       uuid,
  rv_item_id         uuid REFERENCES rv_item(id) ON DELETE SET NULL,
  artifact           text NOT NULL,      -- OPConsultRecord | PrescriptionRecord | DiagnosticReportRecord | ...
  ig_package         text,
  bundle_json        jsonb NOT NULL,
  status             text NOT NULL DEFAULT 'draft',   -- draft | ready_to_share
  validator          text,
  validation_ok      boolean,
  validation_issues  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cn_bundle_patient ON cn_fhir_bundle (canonical_patient_id);
""")


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS cn_fhir_bundle, rv_action, rv_item_element, rv_item,
      ai_clinical_verification, ai_suggestion, ai_event,
      fin_claim, fin_payment, fin_invoice, fin_charge, fin_preauth, fin_coverage,
      ops_alert, ops_device_observation, ops_device, ops_nursing_event, ops_nursing_assessment,
      ops_transfer, ops_admission, ops_bed, ops_room, ops_ward, ops_appointment,
      cn_audit_event, cn_provenance, cn_consent, cn_document, cn_referral, cn_goal, cn_care_plan,
      cn_procedure, cn_imaging_study, cn_diagnostic_report, cn_lab_result, cn_specimen, cn_lab_order,
      cn_medication_dispense, cn_medication_administration, cn_medication_order, cn_medication,
      cn_observation, cn_allergy, cn_condition, cn_chief_complaint, cn_clinical_note,
      cn_encounter_participant, cn_encounter, cn_patient_related_person, cn_patient_address,
      cn_patient_contact, cn_patient_identifier, cn_patient, cn_location, cn_organization,
      cn_practitioner CASCADE;
    """)
