"""In-house model gateway: one long-lived process holding the vision-language
model. The rest of the system calls it over HTTP (see ``cdi_adapter.ml.client``).

Backends:
  stub  - deterministic, no GPU (tests / CI)
  hf    - transformers + Qwen2.5-VL-7B (bf16); on a failed (OOM) load, Qwen2-VL-7B in 8-bit

Run:  python -m cdi_adapter.mlserve   (honours CDI_MLSERVE_BACKEND, CDI_VLM_MODEL_ID)
"""
