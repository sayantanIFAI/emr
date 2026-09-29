"""CPU OCR host: RapidOCR (printed) + TrOCR (handwriting) behind one HTTP service.

Runs on CPU so the GPU stays reserved for Qwen2.5-VL (mlserve). Start with
``python -m cdi_adapter.ocrhost`` and point the pipeline at it with CDI_OCRHOST_URL;
leave CDI_OCRHOST_URL blank to run the same engines in-process.
"""
