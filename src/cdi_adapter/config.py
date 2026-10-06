from __future__ import annotations

from functools import lru_cache
from typing import Literal

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

    # --- object storage: any S3 API (SeaweedFS in the stack, docs/object-store.md) ---
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
    vlm_fallback_model_id: str = "Qwen/Qwen2-VL-7B-Instruct"   # OOM-only; replaced the 3B (licence)
    # The OOM fallback is as large as the primary: in bf16 it would run out of memory in exactly
    # the situation it exists for, so it loads in 8-bit (bitsandbytes LLM.int8, hf backend only;
    # "" = bf16, for a host that can hold two bf16 7B models in turn). Not measured on the target
    # GPU yet: docs/fallback-model.md.
    vlm_fallback_quantize: Literal["", "8bit"] = "8bit"
    vlm_max_pixels_classify: int = 1_000_000
    vlm_max_pixels_ocr: int = 2_000_000   # keep activations modest on a 24 GB card
    vlm_dtype: str = "bfloat16"
    # --- vllm backend (separate `vllm serve` process, OpenAI-compatible) ---
    vllm_url: str = "http://127.0.0.1:8078/v1"
    vllm_model: str = ""                    # blank -> use vlm_model_id
    vllm_timeout_s: float = 240.0
    vllm_guided: bool = True                # token-level JSON-schema decoding (xgrammar).
                                            # adds grammar-mask cost per token; turn off to
                                            # rely on client-side repair + retry instead.
    vllm_guided_backend: str = "xgrammar"

    # --- future: vLLM OpenAI endpoint for the DSLM / guided decoding ---
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: str = "not-needed-local"
    dslm_model: str = "cdi-dslm"

    # --- OCR ---
    ocr_engine: str = "rapidocr"          # rapidocr | none
    ocr_min_conf: float = 0.30
    handwritten_uses_vlm: bool = True

    # --- recognition v2 (ARCHITECTURE §15): regions -> independent engines -> evidence ---
    recognition_v2: bool = True           # False = legacy page-level VLM transcription (§5 S3)
    # CPU OCR host (RapidOCR + TrOCR). Blank = run the engines in-process.
    ocrhost_url: str = ""                 # e.g. http://127.0.0.1:8079
    ocrhost_port: int = 8079
    ocrhost_timeout_s: float = 120.0
    trocr_enabled: bool = True
    trocr_model_id: str = "microsoft/trocr-base-handwritten"   # English (IAM); Bengali is E2-S10
    trocr_max_new_tokens: int = 64
    trocr_batch_size: int = 8
    qwen_line_mode: str = "crop"          # crop = independent read per line crop | off
    qwen_line_max_tokens: int = 48
    # an engine reading is "the same" as another when, after numeric-context
    # normalisation, every number matches exactly AND the text similarity is >= this
    engine_agree_similarity: float = 0.85
    # L8 Qwen adjudication of a disagreement - ADVISORY: orders the two readings for the
    # reviewer, never resolves the disagreement (the fact still goes to review)
    qwen_adjudication_enabled: bool = False
    qwen_adjudication_max_tokens: int = 8

    # --- image quality gate (E2-S12) ---
    quality_gate_mode: str = "enforce"    # enforce = hold for rescan | warn = record only | off
    quality_min_blur_var: float = 25.0    # variance of the Laplacian; sharp 200-dpi scans >> 100
    quality_min_short_side_px: int = 600
    quality_max_glare_frac: float = 0.25  # share of page area in saturated blobs
    quality_max_dark_frac: float = 0.60   # share of page that is near-black (clipped/underexposed)

    # --- pixel grounding (E4-S4) ---
    grounding_enabled: bool = True
    grounding_margin_frac: float = 0.15
    grounding_min_similarity: float = 0.72

    # --- per-field gate policy (E4-S2/S3). Values are ASSUMED until fitted on adjudicated
    # data; engine disagreement, single-engine handwriting and failed grounding always review.
    gate_policy_enabled: bool = True
    gate_policy_path: str = ""            # optional JSON overriding validate/policy.DEFAULT_POLICY

    # --- practitioner link (E6-S11 / E18) ---
    practitioner_min_link_conf: float = 0.90

    # --- file listener (E16) ---
    listener_connector: str = "local"     # local | onedrive | sharepoint | gdrive | pkg.module:Class
    listener_root: str = "./data/listener"            # local: folder; graph/gdrive: folder path in the drive
    listener_inbox: str = "inbox"         # "." = the root folder itself is the inbox
    listener_processing: str = "processing"
    listener_completed: str = "success"   # logical name stays "completed"
    listener_error: str = "error"
    listener_quarantine: str = "quarantine"
    listener_log: str = "log"             # failure-reason notes only
    listener_poll_seconds: float = 10.0
    listener_batch_size: int = 3          # files picked and processed together as one batch
    listener_batch_wait_seconds: float = 60.0   # a partial batch is flushed after this long (0 = never wait)
    listener_stable_polls: int = 2        # size/etag unchanged across this many polls = upload done
    listener_lease_seconds: int = 900
    listener_max_bytes: int = 50_000_000
    listener_patterns: tuple[str, ...] = ("*.pdf", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff")
    listener_max_attempts: int = 3        # hard cap, also enforced by a DB CHECK
    listener_pipeline: str = "inline"     # inline = run every stage in the listener | celery
    listener_retry_base_seconds: int = 60 # recovery agent back-off: base * 2^(attempt-1)
    graph_auth: str = "app"               # app (client credentials, admin consent) | device_code (sign in once)
    graph_scopes: tuple[str, ...] = ("Files.ReadWrite",)   # delegated scopes (device_code only)
    graph_token_cache: str = "./data/graph_token_cache.json"   # device_code: refresh-token cache (keep on a persistent disk)
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""         # prefer certificate auth in production
    graph_cert_path: str = ""
    graph_cert_thumbprint: str = ""
    graph_drive_id: str = ""              # OneDrive: the drive id (or leave blank + graph_user_id)
    graph_user_id: str = ""
    graph_site_id: str = ""               # SharePoint: site id; library resolved to its default drive
    # Google Drive connector (CDI_LISTENER_CONNECTOR=gdrive; pip install '.[gdrive]')
    gdrive_auth: str = "service_account"  # service_account | oauth
    gdrive_credentials_file: str = ""     # service-account key (JSON)
    gdrive_impersonate_user: str = ""     # domain-wide delegation: act as this user
    gdrive_client_id: str = ""            # oauth mode
    gdrive_client_secret: str = ""
    gdrive_refresh_token: str = ""
    gdrive_root_folder_id: str = ""       # listener root folder id (else CDI_LISTENER_ROOT as a path)
    gdrive_drive_id: str = ""             # a shared drive id (optional)

    # --- FHIR builder agent + blob store (E17) ---
    fhir_agent_poll_seconds: float = 5.0
    fhir_agent_max_attempts: int = 3

    # --- downstream screen dispatch (E20) - disabled per target until approved ---
    dispatch_poll_seconds: float = 30.0
    dispatch_max_attempts: int = 3

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
    # Anything the OOM fallback model read is never auto-accepted: it is an older model
    # generation, loaded in 8-bit, and not benchmarked on handwritten prescriptions. Turn off
    # only after the benchmark in docs/fallback-model.md shows parity with the primary.
    gate_fallback_review: bool = True
    gate_partial_penalty: float = 0.30    # confidence subtracted when extraction was _partial
    gate_medication_always_review: bool = True   # any med line with missing dose/route/freq -> review
    gate_local_only_review_types: tuple[str, ...] = ("condition", "medication", "allergy", "procedure")
    audit_sample_rate: float = 0.10       # fraction of gate_audit tier pulled for QA


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
