"""Stage S9 - deterministic FHIR R4 projection + ABDM record artifacts.

Reads ``clinical_fact`` (+ terminology bindings + provenance) and emits FHIR R4
resources wrapped in an ABDM ``Composition``-based ``Bundle(type=document)`` -
one artifact per source document (PrescriptionRecord, DiagnosticReportRecord,
OPConsultRecord, DischargeSummaryRecord, WellnessRecord, HealthDocumentRecord),
each carrying the original scan as a ``DocumentReference`` and a ``Provenance``
chain back to the pixels. No LLM here - pure mapping.
"""
from .service import project_document, project_patient

__all__ = ["project_document", "project_patient"]
