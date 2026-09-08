"""Stage S4 - schema-locked clinical extraction.

For each document, the VLM (via the model gateway) fills the JSON Schema for its
doc_type, citing OCR block ids as evidence for every value. The payload is stored
verbatim in ``extraction``, then deterministically converted into
``clinical_fact`` (+ ``medication_detail``) rows, each with a ``fact_provenance``
row binding it to the pixels it came from.
"""
from .service import extract_document

__all__ = ["extract_document"]
