"""Upload UI + JSON API: drop a patient's scanned documents, get ABDM FHIR back.

Runs the full pipeline S1->S9 synchronously in a background thread per job:
  ingest -> classify (VLM) -> OCR -> extract (VLM, schema-locked) ->
  terminology bind (seed SNOMED/LOINC) -> FHIR projection (ABDM record artifacts).

Serve:  python -m cdi_adapter.webapp    (0.0.0.0:8080)
"""
