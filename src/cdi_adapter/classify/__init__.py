"""Stage S2 - document classification.

One VLM call over the page thumbnails yields: doc_type, specialty, language mix,
handwritten?, and (for multi-doc scans) page spans. Written to
``doc_classification`` and used to route S3 (OCR) and S4 (extraction schema).
"""
