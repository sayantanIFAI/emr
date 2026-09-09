from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Override via env vars (prefix ``CDI_``) or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CDI_", extra="ignore", case_sensitive=False
    )

    env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # --- datastore ---
    database_url: str = "postgresql+psycopg://cdi:cdi@localhost:5432/cdi"
    redis_url: str = "redis://localhost:6379/0"

    # --- object storage (MinIO / S3) ---
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "cdiadmin"
    s3_secret_key: str = "cdiadminsecret"
    s3_bucket: str = "cdi-documents"
    s3_region: str = "us-east-1"
    s3_use_path_style: bool = True

    # --- ingestion ---
    inbox_dir: str = "./data/inbox"
    processed_dir: str = "./data/processed"
    failed_dir: str = "./data/failed"
    watch_settle_seconds: float = 2.0
    page_dpi: int = 200
    max_pages: int = 200
    allowed_mime_prefixes: tuple[str, ...] = ("image/", "application/pdf")

    # --- image preprocessing ---
    deskew_enabled: bool = True
    denoise_enabled: bool = True
    max_deskew_deg: float = 15.0

    # --- pipeline / jurisdiction packages ---
    ig_package: str = "nrces.fhir.r4.ndhm#6.5.0"
    terminology_package: str = "in-snomed-loinc-icd10"

    # --- web app (upload UI + FHIR API) ---
    webapp_port: int = 8080   # RunPod proxies external 8081 -> localhost:8080

    # --- model gateway (cdi_adapter.mlserve) ---
    mlserve_url: str = "http://127.0.0.1:8077"
    mlserve_port: int = 8077
    mlserve_backend: str = "stub"          # stub | hf | vllm
    vlm_model_id: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    vlm_fallback_model_id: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    vlm_max_pixels_classify: int = 1_000_000
    vlm_max_pixels_ocr: int = 2_000_000   # keep activations modest on a 24 GB card
    vlm_dtype: str = "bfloat16"
    # --- vllm backend (separate `vllm serve` process, OpenAI-compatible) ---
    vllm_url: str = "http://127.0.0.1:8078/v1"
    vllm_model: str = ""                    # blank -> use vlm_model_id
    vllm_timeout_s: float = 240.0
    vllm_guided_backend: str = "xgrammar"   # token-level JSON-schema decoding

    # --- future: vLLM OpenAI endpoint for the DSLM / guided decoding ---
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: str = "not-needed-local"
    dslm_model: str = "cdi-dslm"

    # --- OCR ---
    ocr_engine: str = "rapidocr"          # rapidocr | none
    ocr_min_conf: float = 0.30
    handwritten_uses_vlm: bool = True

    # --- throughput ---
    job_max_workers: int = 5              # documents ingested/classified/OCR'd concurrently;
                                         # _cpu.py sizes native thread pools to
                                         # (cpu_budget - 1) / this  (keep them in sync)
    fast_classify: bool = True           # try the scored heuristic classifier first; it only
                                         # short-circuits the VLM on an unambiguous, cleanly
                                         # OCR'd page - everything else still goes to the VLM
    extract_retries: int = 1             # VLM extraction re-tries on schema failure (schemas
                                         # were relaxed so a first-pass slip is now rare)
    extract_concurrency: int = 4         # concurrent VLM extract calls in flight (vllm backend
                                         # batches them; 1 = the old serial behaviour for `hf`)
    extract_max_tokens: int = 1400       # base; long doc types get more (see extract/prompt.py)

    # --- S6 governance gate (fact -> auto_accepted | in_review) ---
    gate_auto_accept_conf: float = 0.985  # >= this AND clean -> auto_accepted
    gate_audit_conf: float = 0.95         # >= this AND clean -> auto_accepted + audit sample
    gate_review_floor: float = 0.85       # < gate_audit_conf -> in_review
    gate_partial_penalty: float = 0.30    # confidence subtracted when extraction was _partial
    gate_medication_always_review: bool = True   # any med line with missing dose/route/freq -> review
    gate_local_only_review_types: tuple[str, ...] = ("condition", "medication", "allergy", "procedure")
    audit_sample_rate: float = 0.10       # fraction of gate_audit tier pulled for QA


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
