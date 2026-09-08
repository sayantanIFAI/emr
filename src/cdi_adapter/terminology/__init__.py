"""Stage S5 - terminology normalization (seed edition).

Binds each clinical_fact's free text to SNOMED CT / LOINC / ICD-10 / UCUM using a
curated seed map plus alias/fuzzy fallback. This is the offline, deterministic
stand-in for the full Snowstorm + SapBERT/FAISS service described in DESIGN.md
Part C.5 - good enough to emit coded FHIR for the common Indian OPD vocabulary;
anything unmatched is kept as ``code_status='local_only'`` with its verbatim text.
"""
from .service import bind_document

__all__ = ["bind_document"]
