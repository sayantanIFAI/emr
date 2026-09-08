"""Stage S6 - clinical validation + the governance gate.

Deterministic (no LLM) checks over every extracted clinical_fact:
physiological ranges, unit sanity, dose / frequency ranges, impossible dates,
patient/encounter consistency, duplicates and cross-document contradictions,
evidence-present. The gate then routes each fact:

    clean + high confidence + not _partial          -> auto_accepted
    blocker rule | _partial | low confidence |      -> in_review  (+ review_task)
    unmapped critical concept | ambiguous medication

Nothing malformed or uncertain silently becomes trusted clinical data. FHIR
projection only *asserts* facts whose review_state is auto_accepted /
clinician_confirmed / corrected.
"""
from .service import validate_document

__all__ = ["validate_document"]
