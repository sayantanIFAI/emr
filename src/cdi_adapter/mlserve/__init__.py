"""In-house model gateway: one long-lived process holding the vision-language
model. The rest of the system calls it over HTTP (see ``cdi_adapter.ml.client``).

Backends:
  stub  - deterministic, no GPU (tests / CI)
  hf    - transformers + Qwen2.5-VL (bf16); 7B primary, 3B fallback

Run:  python -m cdi_adapter.mlserve   (honours CDI_MLSERVE_BACKEND, CDI_VLM_MODEL_ID)
"""
