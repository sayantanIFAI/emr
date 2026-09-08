-- Read-model views: what clinicians actually look at.
-- Applied after schema.sql. Safe to re-run.

DROP MATERIALIZED VIEW IF EXISTS v_problem_list;
CREATE MATERIALIZED VIEW v_problem_list AS
  SELECT patient_id,
         code_system, code, code_display,
         min(local_text)       AS local_text,
         max(clinical_status)  AS clinical_status,
         max(verification)     AS verification,
         min(onset)            AS onset,
         max(effective_time)   AS last_seen,
         array_agg(DISTINCT id) AS fact_ids,
         max(confidence_overall) AS confidence
  FROM clinical_fact
  WHERE fact_type = 'condition'
    AND is_current
    AND review_state IN ('auto_accepted','clinician_confirmed','corrected')
  GROUP BY patient_id, code_system, code, code_display;

DROP MATERIALIZED VIEW IF EXISTS v_medication_list;
CREATE MATERIALIZED VIEW v_medication_list AS
  SELECT f.patient_id,
         md.drug_text,
         md.rxlike_system, md.rxlike_code,
         md.strength_num, md.strength_unit,
         md.dose_num, md.dose_unit_ucum, md.route, md.frequency_code,
         f.clinical_status,
         max(f.effective_time) AS last_seen,
         array_agg(DISTINCT f.id) AS fact_ids,
         max(f.confidence_overall) AS confidence
  FROM clinical_fact f
  JOIN medication_detail md ON md.fact_id = f.id
  WHERE f.fact_type = 'medication'
    AND f.is_current
    AND f.review_state IN ('auto_accepted','clinician_confirmed','corrected')
  GROUP BY f.patient_id, md.drug_text, md.rxlike_system, md.rxlike_code,
           md.strength_num, md.strength_unit, md.dose_num, md.dose_unit_ucum,
           md.route, md.frequency_code, f.clinical_status;

DROP MATERIALIZED VIEW IF EXISTS v_allergy_list;
CREATE MATERIALIZED VIEW v_allergy_list AS
  SELECT patient_id, code_system, code, code_display,
         min(local_text) AS local_text,
         max(clinical_status) AS clinical_status,
         array_agg(DISTINCT id) AS fact_ids,
         max(confidence_overall) AS confidence
  FROM clinical_fact
  WHERE fact_type = 'allergy'
    AND is_current
    AND review_state IN ('auto_accepted','clinician_confirmed','corrected')
  GROUP BY patient_id, code_system, code, code_display;

DROP VIEW IF EXISTS v_results_grid;
CREATE VIEW v_results_grid AS
  SELECT patient_id,
         code_system, code, code_display,
         effective_time::date AS result_date,
         value_num, value_unit_ucum,
         ref_range_low, ref_range_high, ref_range_text, abnormal_flag,
         confidence_overall, review_state, id AS fact_id
  FROM clinical_fact
  WHERE fact_type IN ('lab_result','vital_sign')
    AND is_current;

DROP VIEW IF EXISTS v_encounter_timeline;
CREATE VIEW v_encounter_timeline AS
  SELECT e.id AS encounter_id, e.patient_id, e.class, e.period_start, e.period_end,
         e.specialty, e.review_state,
         count(f.id) FILTER (WHERE f.fact_type = 'condition')   AS n_conditions,
         count(f.id) FILTER (WHERE f.fact_type = 'medication')  AS n_medications,
         count(f.id) FILTER (WHERE f.fact_type = 'lab_result')  AS n_labs,
         count(f.id) FILTER (WHERE f.fact_type = 'procedure')   AS n_procedures
  FROM encounter e
  LEFT JOIN clinical_fact f ON f.encounter_id = e.id AND f.is_current
  GROUP BY e.id;
