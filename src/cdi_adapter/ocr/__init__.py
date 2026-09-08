"""Stage S3 - OCR / layout / handwriting (the evidence layer).

- printed documents  -> RapidOCR (ONNX, CPU): precise line boxes + confidence
- handwritten docs   -> VLM transcription via the model gateway (line text; page-level box)

Output: ``ocr_block`` rows (text + bbox + confidence + reading order) that every
downstream clinical fact will cite as provenance.
"""
