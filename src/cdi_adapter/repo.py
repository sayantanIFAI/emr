"""Thin data-access layer over the adapter schema.

Raw SQL against the authoritative DDL (db/schema.sql) so there is no ORM drift.
Every function takes a live SQLAlchemy Session.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


# --------------------------------------------------------------------------- #
# source_document
# --------------------------------------------------------------------------- #
def get_document_by_sha(sess: Session, sha256: str) -> dict[str, Any] | None:
    row = sess.execute(
        text("SELECT * FROM source_document WHERE sha256 = :sha"), {"sha": sha256}
    ).mappings().first()
    return dict(row) if row else None


def get_document(sess: Session, document_id: UUID | str) -> dict[str, Any] | None:
    row = sess.execute(
        text("SELECT * FROM source_document WHERE id = :id"), {"id": str(document_id)}
    ).mappings().first()
    return dict(row) if row else None


def insert_source_document(
    sess: Session,
    *,
    sha256: str,
    mime_type: str,
    object_uri: str,
    byte_size: int,
    source_channel: str,
    original_filename: str | None = None,
    legacy_ref: str | None = None,
    legacy_patient_ref: str | None = None,
    captured_at: Any | None = None,
) -> UUID:
    row = sess.execute(
        text(
            """
            INSERT INTO source_document
              (sha256, mime_type, object_uri, byte_size, source_channel,
               original_filename, legacy_ref, legacy_patient_ref, captured_at, status)
            VALUES
              (:sha256, :mime_type, :object_uri, :byte_size, :source_channel,
               :original_filename, :legacy_ref, :legacy_patient_ref, :captured_at, 'received')
            RETURNING id
            """
        ),
        {
            "sha256": sha256,
            "mime_type": mime_type,
            "object_uri": object_uri,
            "byte_size": byte_size,
            "source_channel": source_channel,
            "original_filename": original_filename,
            "legacy_ref": legacy_ref,
            "legacy_patient_ref": legacy_patient_ref,
            "captured_at": captured_at,
        },
    ).scalar_one()
    return row


def set_document_status(
    sess: Session, document_id: UUID | str, status: str, *,
    page_count: int | None = None, error_detail: str | None = None,
) -> None:
    sess.execute(
        text(
            """
            UPDATE source_document
               SET status = :status,
                   page_count = COALESCE(:page_count, page_count),
                   error_detail = :error_detail
             WHERE id = :id
            """
        ),
        {
            "id": str(document_id),
            "status": status,
            "page_count": page_count,
            "error_detail": error_detail,
        },
    )


# --------------------------------------------------------------------------- #
# document_page
# --------------------------------------------------------------------------- #
def insert_document_page(
    sess: Session,
    *,
    document_id: UUID | str,
    page_no: int,
    image_uri: str,
    width_px: int,
    height_px: int,
    dpi: int,
    preproc: dict[str, Any],
    thumb_uri: str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO document_page
              (document_id, page_no, image_uri, thumb_uri, width_px, height_px, dpi, preproc)
            VALUES
              (:document_id, :page_no, :image_uri, :thumb_uri, :width_px, :height_px, :dpi,
               CAST(:preproc AS jsonb))
            ON CONFLICT (document_id, page_no) DO UPDATE
              SET image_uri = EXCLUDED.image_uri,
                  thumb_uri = EXCLUDED.thumb_uri,
                  width_px = EXCLUDED.width_px,
                  height_px = EXCLUDED.height_px,
                  dpi = EXCLUDED.dpi,
                  preproc = EXCLUDED.preproc
            RETURNING id
            """
        ),
        {
            "document_id": str(document_id),
            "page_no": page_no,
            "image_uri": image_uri,
            "thumb_uri": thumb_uri,
            "width_px": width_px,
            "height_px": height_px,
            "dpi": dpi,
            "preproc": json.dumps(preproc),
        },
    ).scalar_one()


def list_document_pages(sess: Session, document_id: UUID | str) -> list[dict[str, Any]]:
    rows = sess.execute(
        text(
            "SELECT * FROM document_page WHERE document_id = :id ORDER BY page_no"
        ),
        {"id": str(document_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# pipeline_run
# --------------------------------------------------------------------------- #
def start_pipeline_run(
    sess: Session,
    *,
    document_id: UUID | str,
    stage: str,
    model_name: str | None = None,
    model_version: str | None = None,
    model_sha: str | None = None,
    params: dict[str, Any] | None = None,
    input_sha: str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO pipeline_run
              (document_id, stage, status, model_name, model_version, model_sha, params, input_sha)
            VALUES
              (:document_id, :stage, 'running', :model_name, :model_version, :model_sha,
               CAST(:params AS jsonb), :input_sha)
            RETURNING id
            """
        ),
        {
            "document_id": str(document_id),
            "stage": stage,
            "model_name": model_name,
            "model_version": model_version,
            "model_sha": model_sha,
            "params": json.dumps(params or {}),
            "input_sha": input_sha,
        },
    ).scalar_one()


def finish_pipeline_run(
    sess: Session,
    run_id: UUID | str,
    *,
    status: str,
    metrics: dict[str, Any] | None = None,
    output_sha: str | None = None,
    error_detail: str | None = None,
) -> None:
    sess.execute(
        text(
            """
            UPDATE pipeline_run
               SET status = :status,
                   ended_at = now(),
                   metrics = CAST(:metrics AS jsonb),
                   output_sha = :output_sha,
                   error_detail = :error_detail
             WHERE id = :id
            """
        ),
        {
            "id": str(run_id),
            "status": status,
            "metrics": json.dumps(metrics or {}),
            "output_sha": output_sha,
            "error_detail": error_detail,
        },
    )


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def insert_extraction(
    sess: Session,
    *,
    document_id: UUID | str,
    schema_name: str,
    schema_version: str,
    payload: dict[str, Any],
    evidence_map: dict[str, Any] | None = None,
    model_run_id: UUID | str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO extraction
              (document_id, schema_name, schema_version, payload, evidence_map, model_run_id)
            VALUES
              (:document_id, :schema_name, :schema_version,
               CAST(:payload AS jsonb), CAST(:evidence_map AS jsonb), :model_run_id)
            RETURNING id
            """
        ),
        {
            "document_id": str(document_id),
            "schema_name": schema_name,
            "schema_version": schema_version,
            "payload": json.dumps(payload),
            "evidence_map": json.dumps(evidence_map or {}),
            "model_run_id": str(model_run_id) if model_run_id else None,
        },
    ).scalar_one()


# --------------------------------------------------------------------------- #
# patient_identity / encounter
# --------------------------------------------------------------------------- #
def get_or_create_patient(
    sess: Session,
    *,
    name_given: str | None,
    name_family: str | None,
    abha_number: str | None = None,
    abha_address: str | None = None,
    gender: str | None = None,
    birth_date: Any | None = None,
    legacy_mrn: str | None = None,
) -> UUID:
    if abha_number:
        row = sess.execute(
            text("SELECT id FROM patient_identity WHERE abha_number = :a"),
            {"a": abha_number},
        ).first()
        if row:
            return row[0]
    if legacy_mrn:
        row = sess.execute(
            text("SELECT id FROM patient_identity WHERE legacy_mrn = :m"),
            {"m": legacy_mrn},
        ).first()
        if row:
            return row[0]
    match_status = "abha_verified" if abha_number else "clerk_confirmed"
    match_score = 1.0 if abha_number else 0.9
    return sess.execute(
        text(
            """
            INSERT INTO patient_identity
              (name_given, name_family, abha_number, abha_address, gender, birth_date,
               legacy_mrn, match_status, match_score)
            VALUES (:g, :f, CAST(:a AS text), CAST(:aa AS text), CAST(:gen AS text),
                    CAST(:bd AS date), CAST(:mrn AS text), :ms, :msc)
            RETURNING id
            """
        ),
        {"g": name_given, "f": name_family, "a": abha_number, "aa": abha_address,
         "gen": gender, "bd": birth_date, "mrn": legacy_mrn,
         "ms": match_status, "msc": match_score},
    ).scalar_one()


def create_encounter(
    sess: Session,
    *,
    patient_id: UUID | str,
    enc_class: str,
    period_start: Any | None,
    period_end: Any | None = None,
    period_precision: str = "day",
    specialty: str | None = None,
    facility_ref: str | None = None,
    practitioner_ref: str | None = None,
    derived_from: list[str] | None = None,
    confidence: float = 0.8,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO encounter
              (patient_id, class, period_start, period_end, period_precision, specialty,
               facility_ref, practitioner_ref, derived_from, confidence, review_state)
            VALUES (:p, :c, :ps, :pe, :pp, :sp, :fr, :pr, CAST(:df AS uuid[]), :conf, 'pending')
            RETURNING id
            """
        ),
        {"p": str(patient_id), "c": enc_class, "ps": period_start, "pe": period_end,
         "pp": period_precision, "sp": specialty, "fr": facility_ref, "pr": practitioner_ref,
         "df": derived_from or [], "conf": confidence},
    ).scalar_one()


# --------------------------------------------------------------------------- #
# clinical_fact / medication_detail / fact_provenance
# --------------------------------------------------------------------------- #
def insert_clinical_fact(sess: Session, **f: Any) -> UUID:
    cols = [
        "patient_id", "encounter_id", "fact_type", "code_system", "code", "code_display",
        "code_status", "local_text", "value_kind", "value_num", "value_unit_ucum",
        "value_code_system", "value_code", "value_code_display", "value_text", "value_bool",
        "ref_range_low", "ref_range_high", "ref_range_text", "abnormal_flag",
        "clinical_status", "verification", "onset", "effective_time", "asserted_time",
        "extraction_id", "confidence_overall", "confidence_ocr", "confidence_extract",
        "confidence_terminology", "review_state", "dedup_key",
    ]
    vals = {c: f.get(c) for c in cols}
    vals["code_status"] = vals["code_status"] or "unmapped"
    vals["review_state"] = vals["review_state"] or "pending"
    vals["confidence_overall"] = vals["confidence_overall"] or 0.0
    src = f.get("source_doc_ids") or []
    placeholders = ", ".join(f":{c}" for c in cols)
    return sess.execute(
        text(
            f"""
            INSERT INTO clinical_fact ({", ".join(cols)}, source_doc_ids)
            VALUES ({placeholders}, CAST(:source_doc_ids AS uuid[]))
            RETURNING id
            """
        ),
        {**{c: (str(vals[c]) if c.endswith("_id") and vals[c] else vals[c]) for c in cols},
         "source_doc_ids": [str(x) for x in src]},
    ).scalar_one()


def insert_medication_detail(sess: Session, fact_id: UUID | str, **m: Any) -> None:
    cols = ["drug_text", "rxlike_system", "rxlike_code", "form", "strength_num",
            "strength_unit", "dose_num", "dose_unit_ucum", "route", "frequency_code",
            "frequency_per_day", "duration_days", "prn", "instructions", "intent"]
    vals = {c: m.get(c) for c in cols}
    vals["intent"] = vals["intent"] or "order"
    vals["drug_text"] = vals["drug_text"] or ""
    sess.execute(
        text(
            f"INSERT INTO medication_detail (fact_id, {', '.join(cols)}) "
            f"VALUES (:fact_id, {', '.join(f':{c}' for c in cols)}) "
            f"ON CONFLICT (fact_id) DO UPDATE SET "
            + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        ),
        {"fact_id": str(fact_id), **vals},
    )


def insert_fact_provenance(
    sess: Session,
    *,
    fact_id: UUID | str,
    source_doc_id: UUID | str,
    page_id: UUID | str | None,
    ocr_block_ids: list[str],
    bbox_union: list[int] | None,
    extracted_text: str,
    pipeline_run_ids: list[str],
    model_stack: dict[str, Any],
    agent: str = "system",
) -> None:
    sess.execute(
        text(
            """
            INSERT INTO fact_provenance
              (fact_id, source_doc_id, page_id, ocr_block_ids, bbox_union, extracted_text,
               pipeline_run_ids, model_stack, agent)
            VALUES (:fact_id, :source_doc_id, :page_id, CAST(:ocr_block_ids AS uuid[]),
                    CAST(:bbox_union AS int[]), :extracted_text,
                    CAST(:pipeline_run_ids AS uuid[]), CAST(:model_stack AS jsonb), :agent)
            """
        ),
        {
            "fact_id": str(fact_id),
            "source_doc_id": str(source_doc_id),
            "page_id": str(page_id) if page_id else None,
            "ocr_block_ids": [str(x) for x in ocr_block_ids],
            "bbox_union": bbox_union,
            "extracted_text": extracted_text[:2000],
            "pipeline_run_ids": [str(x) for x in pipeline_run_ids],
            "model_stack": json.dumps(model_stack),
            "agent": agent,
        },
    )


def list_clinical_facts(sess: Session, *, patient_id: UUID | str | None = None,
                        document_id: UUID | str | None = None) -> list[dict[str, Any]]:
    if document_id:
        rows = sess.execute(
            text("SELECT * FROM clinical_fact WHERE :d = ANY(source_doc_ids) AND is_current "
                 "ORDER BY fact_type, created_at"),
            {"d": str(document_id)},
        ).mappings().all()
    else:
        rows = sess.execute(
            text("SELECT * FROM clinical_fact WHERE patient_id = :p AND is_current "
                 "ORDER BY encounter_id, fact_type, created_at"),
            {"p": str(patient_id)},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_medication_detail(sess: Session, fact_id: UUID | str) -> dict[str, Any] | None:
    row = sess.execute(
        text("SELECT * FROM medication_detail WHERE fact_id = :id"), {"id": str(fact_id)}
    ).mappings().first()
    return dict(row) if row else None


def get_fact_provenance(sess: Session, fact_id: UUID | str) -> list[dict[str, Any]]:
    rows = sess.execute(
        text("SELECT * FROM fact_provenance WHERE fact_id = :id ORDER BY recorded_at"),
        {"id": str(fact_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# S6 gate: review state, review tasks, conflicts
# --------------------------------------------------------------------------- #
def set_fact_review(
    sess: Session,
    fact_id: UUID | str,
    *,
    review_state: str,
    confidence_overall: float | None = None,
    review_note: str | None = None,
    reviewed_by: str | None = None,
    reviewed_at_now: bool = False,
) -> None:
    sess.execute(
        text(
            """
            UPDATE clinical_fact
               SET review_state = :rs,
                   confidence_overall = COALESCE(:co, confidence_overall),
                   review_note = COALESCE(:rn, review_note),
                   reviewed_by = COALESCE(:rb, reviewed_by),
                   reviewed_at = CASE WHEN :now THEN now() ELSE reviewed_at END
             WHERE id = :id
            """
        ),
        {"id": str(fact_id), "rs": review_state, "co": confidence_overall,
         "rn": review_note, "rb": reviewed_by, "now": reviewed_at_now},
    )


def apply_fact_correction(sess: Session, fact_id: UUID | str, fields: dict[str, Any],
                          reviewed_by: str, note: str | None) -> None:
    allowed = {"local_text", "code_system", "code", "code_display", "code_status",
               "value_num", "value_unit_ucum", "value_text", "abnormal_flag",
               "clinical_status", "verification", "ref_range_low", "ref_range_high"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        assigns = ", ".join(f"{k} = :{k}" for k in sets)
        sess.execute(
            text(f"UPDATE clinical_fact SET {assigns} WHERE id = :id"),
            {**sets, "id": str(fact_id)},
        )
    set_fact_review(sess, fact_id, review_state="corrected", review_note=note,
                    reviewed_by=reviewed_by, reviewed_at_now=True)


def create_review_task(
    sess: Session,
    *,
    kind: str,
    patient_id: str | None,
    document_id: str | None,
    ref_fact_ids: list[str],
    priority: int = 3,
    payload: dict[str, Any] | None = None,
    ref_conflict_id: str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO review_task
              (patient_id, document_id, kind, ref_fact_ids, ref_conflict_id, priority, payload)
            VALUES (:p, :d, :k, CAST(:f AS uuid[]), :c, :pri, CAST(:pl AS jsonb))
            RETURNING id
            """
        ),
        {"p": patient_id, "d": document_id, "k": kind,
         "f": [str(x) for x in ref_fact_ids], "c": ref_conflict_id,
         "pri": priority, "pl": json.dumps(payload or {})},
    ).scalar_one()


def close_review_task(sess: Session, task_id: UUID | str, status: str = "done") -> None:
    sess.execute(
        text("UPDATE review_task SET status = :s, closed_at = now() WHERE id = :id"),
        {"id": str(task_id), "s": status},
    )


def list_review_tasks(sess: Session, *, patient_id: str | None = None,
                      open_only: bool = True) -> list[dict[str, Any]]:
    q = "SELECT * FROM review_task WHERE 1=1"
    p: dict[str, Any] = {}
    if open_only:
        q += " AND status IN ('queued','in_progress')"
    if patient_id:
        q += " AND patient_id = :p"
        p["p"] = patient_id
    q += " ORDER BY priority, created_at"
    return [dict(r) for r in sess.execute(text(q), p).mappings().all()]


def insert_fact_conflict(
    sess: Session,
    *,
    patient_id: str,
    fact_a: str,
    fact_b: str | None,
    conflict_type: str,
    evidence_state: str,
    severity: str,
    auto_resolution: str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO fact_conflict
              (patient_id, fact_a, fact_b, conflict_type, evidence_state, severity,
               auto_resolution, status)
            VALUES (:p, :a, :b, :ct, :es, :sev, CAST(:ar AS text), :st)
            RETURNING id
            """
        ),
        {"p": patient_id, "a": fact_a, "b": fact_b, "ct": conflict_type,
         "es": evidence_state, "sev": severity, "ar": auto_resolution,
         "st": "auto_resolved" if auto_resolution else "open"},
    ).scalar_one()


def find_similar_current_facts(sess: Session, *, patient_id: str, fact_type: str,
                               code: str | None, exclude_id: str) -> list[dict[str, Any]]:
    rows = sess.execute(
        text(
            """
            SELECT * FROM clinical_fact
             WHERE patient_id = :p AND fact_type = :ft AND is_current
               AND id <> :ex
               AND (code IS NOT DISTINCT FROM :code OR :code IS NULL)
            """
        ),
        {"p": patient_id, "ft": fact_type, "code": code, "ex": exclude_id},
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# fhir_resource / fhir_bundle
# --------------------------------------------------------------------------- #
def upsert_fhir_resource(
    sess: Session,
    *,
    patient_id: UUID | str | None,
    encounter_id: UUID | str | None,
    resource_type: str,
    fhir_id: str,
    profile: list[str],
    resource: dict[str, Any],
    derived_from_facts: list[str],
    validation_status: str = "pending",
    validation_issues: list[Any] | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO fhir_resource
              (patient_id, encounter_id, resource_type, fhir_id, version_id, profile,
               resource, derived_from_facts, validation_status, validation_issues)
            VALUES (:p, :e, :rt, :fid, 1, CAST(:profile AS text[]), CAST(:res AS jsonb),
                    CAST(:dff AS uuid[]), :vs, CAST(:vi AS jsonb))
            ON CONFLICT (resource_type, fhir_id, version_id) DO UPDATE
              SET resource = EXCLUDED.resource, profile = EXCLUDED.profile,
                  validation_status = EXCLUDED.validation_status,
                  validation_issues = EXCLUDED.validation_issues
            RETURNING id
            """
        ),
        {"p": str(patient_id) if patient_id else None,
         "e": str(encounter_id) if encounter_id else None,
         "rt": resource_type, "fid": fhir_id, "profile": profile,
         "res": json.dumps(resource), "dff": [str(x) for x in derived_from_facts],
         "vs": validation_status, "vi": json.dumps(validation_issues or [])},
    ).scalar_one()


def insert_fhir_bundle(
    sess: Session,
    *,
    patient_id: UUID | str,
    encounter_id: UUID | str | None,
    artifact_type: str,
    bundle: dict[str, Any],
    bundle_hash: str,
    ig_package: str,
    validation_status: str = "pending",
    status: str = "draft",
    care_context: str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO fhir_bundle
              (patient_id, encounter_id, care_context, artifact_type, bundle, bundle_hash,
               validation_status, fhir_version, ig_package, status)
            VALUES (:p, :e, :cc, :at, CAST(:b AS jsonb), :bh, :vs, '4.0.1', :ig, :st)
            RETURNING id
            """
        ),
        {"p": str(patient_id), "e": str(encounter_id) if encounter_id else None,
         "cc": care_context, "at": artifact_type, "b": json.dumps(bundle),
         "bh": bundle_hash, "vs": validation_status, "ig": ig_package, "st": status},
    ).scalar_one()


def list_fhir_bundles(sess: Session, patient_id: UUID | str) -> list[dict[str, Any]]:
    rows = sess.execute(
        text("SELECT * FROM fhir_bundle WHERE patient_id = :p ORDER BY created_at"),
        {"p": str(patient_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# doc_classification
# --------------------------------------------------------------------------- #
def insert_doc_classification(
    sess: Session,
    *,
    document_id: UUID | str,
    doc_type: str,
    is_handwritten: bool,
    languages: list[str],
    confidence: float,
    specialty: str | None = None,
    page_spans: list[dict[str, Any]] | None = None,
    model_run_id: UUID | str | None = None,
) -> UUID:
    return sess.execute(
        text(
            """
            INSERT INTO doc_classification
              (document_id, doc_type, specialty, language, is_handwritten,
               page_spans, confidence, model_run_id)
            VALUES
              (:document_id, :doc_type, :specialty, CAST(:language AS text[]), :is_handwritten,
               CAST(:page_spans AS jsonb), :confidence, :model_run_id)
            RETURNING id
            """
        ),
        {
            "document_id": str(document_id),
            "doc_type": doc_type,
            "specialty": specialty,
            "language": languages,
            "is_handwritten": is_handwritten,
            "page_spans": json.dumps(page_spans or []),
            "confidence": confidence,
            "model_run_id": str(model_run_id) if model_run_id else None,
        },
    ).scalar_one()


def get_doc_classification(sess: Session, document_id: UUID | str) -> dict[str, Any] | None:
    row = sess.execute(
        text(
            "SELECT * FROM doc_classification WHERE document_id = :id "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"id": str(document_id)},
    ).mappings().first()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# ocr_block
# --------------------------------------------------------------------------- #
def delete_ocr_blocks_for_document(sess: Session, document_id: UUID | str) -> int:
    return sess.execute(
        text(
            """
            DELETE FROM ocr_block
             WHERE page_id IN (SELECT id FROM document_page WHERE document_id = :id)
            """
        ),
        {"id": str(document_id)},
    ).rowcount


def insert_ocr_blocks(sess: Session, blocks: list[dict[str, Any]]) -> int:
    """Bulk insert. Each dict: page_id, block_type, reading_order, text, bbox(list[int]),
    ocr_conf, lang, polygon(optional list), table_ref, row_idx, col_idx, model_run_id."""
    if not blocks:
        return 0
    sess.execute(
        text(
            """
            INSERT INTO ocr_block
              (page_id, block_type, reading_order, text, bbox, polygon, ocr_conf,
               lang, table_ref, row_idx, col_idx, model_run_id)
            VALUES
              (:page_id, :block_type, :reading_order, :text, CAST(:bbox AS int[]),
               CAST(:polygon AS jsonb),
               :ocr_conf, :lang, :table_ref, :row_idx, :col_idx, :model_run_id)
            """
        ),
        [
            {
                "page_id": str(b["page_id"]),
                "block_type": b.get("block_type", "line"),
                "reading_order": b.get("reading_order"),
                "text": b["text"],
                "bbox": [int(x) for x in b["bbox"]],
                "polygon": json.dumps(b["polygon"]) if b.get("polygon") is not None else None,
                "ocr_conf": float(b.get("ocr_conf", 0.0)),
                "lang": b.get("lang"),
                "table_ref": str(b["table_ref"]) if b.get("table_ref") else None,
                "row_idx": b.get("row_idx"),
                "col_idx": b.get("col_idx"),
                "model_run_id": str(b["model_run_id"]) if b.get("model_run_id") else None,
            }
            for b in blocks
        ],
    )
    return len(blocks)


def list_ocr_blocks(sess: Session, document_id: UUID | str) -> list[dict[str, Any]]:
    rows = sess.execute(
        text(
            """
            SELECT ob.*, dp.page_no
              FROM ocr_block ob
              JOIN document_page dp ON dp.id = ob.page_id
             WHERE dp.document_id = :id
             ORDER BY dp.page_no, ob.reading_order NULLS LAST
            """
        ),
        {"id": str(document_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# audit_log
# --------------------------------------------------------------------------- #
def write_audit(
    sess: Session,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: str | None = None,
    patient_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    sess.execute(
        text(
            """
            INSERT INTO audit_log (actor, action, entity, entity_id, patient_id, detail, request_id)
            VALUES (:actor, :action, :entity, :entity_id, :patient_id, CAST(:detail AS jsonb), :request_id)
            """
        ),
        {
            "actor": actor,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "patient_id": patient_id,
            "detail": json.dumps(detail or {}),
            "request_id": request_id,
        },
    )
