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
    # A file with more pages than this is refused, not cut short (LS-S3). 20 is a PLACEHOLDER: the story
    # assumes about 10 and the real longest prescription is not known; the owner confirms the number.
    max_pages: int = 20
    allowed_mime_prefixes: tuple[str, ...] = ("image/", "application/pdf")

    # --- output connector (UP-S3): what a finished document is turned into for downstream ---
    output_connector: str = "json_placeholder"      # the real HIS / EMR contract replaces it (result.v2)
    # where the finished result JSON is also written when a document is validated (OUT-S2). Blank / false =
    # not written: the endpoint still builds it from the database on demand.
    output_dir: str = ""
    output_object_store: bool = False

    # --- correction loop: human corrections -> per-doctor lexicon -> class-C alias ---
    correction_max_len: int = 500
    # a doctor's "X means Y" becomes an alias for that doctor after this many confirmations by
    # reviewers (and only if Y names exactly one concept). PLACEHOLDER: tune on real corrections.
    doctor_alias_min_verified: int = 3
    profile_cache_ttl_s: int = 300        # Redis read cache of a doctor's lexicon; the database stays the source of truth

    # --- upload screen (UP-S1) ---
    # What the web upload accepts, decided from the file's bytes (never its name): JPG, PNG, TIFF, PDF.
    upload_mime_types: tuple[str, ...] = ("application/pdf", "image/png", "image/jpeg", "image/tiff")
    # PLACEHOLDER limits (not measured): sized for phone photos (a few MB) and scanned PDFs, and to
    # bound memory per request (the whole upload is read into memory). Tune on pilot data.
    upload_max_files: int = 10
    upload_max_file_bytes: int = 50_000_000
    upload_max_total_bytes: int = 150_000_000

    # --- image preprocessing ---
    deskew_enabled: bool = True
    denoise_enabled: bool = True
    max_deskew_deg: float = 15.0
    # page geometry (IM-S2). All thresholds are PLACEHOLDERS measured on synthetic pages only.
    orient_enabled: bool = True           # turn a sideways page (90 / 270 degrees) upright when the way up is clear
    orient_upright_margin: float = 0.02   # how much clearer one way must be (ink-centroid score); else hold for retake
    # 180 degrees: needs a clear negative score on mixed-case text. OFF: the signal was measured only on
    # synthetic text and real handwriting may bias it, and a wrong 180 turn would ruin an upright page.
    orient_upside_down: bool = False
    upside_down_threshold: float = -0.008
    perspective_enabled: bool = True      # cut the page out of a photo and flatten it when four clear corners are found
    perspective_min_side_px: int = 400    # smaller pictures are not looked at for a page edge
    perspective_min_area_frac: float = 0.30   # the page must cover at least this share of the picture
    perspective_min_move_frac: float = 0.03   # corners at least this far (share of the diagonal) from the picture's corners
    photo_border_contrast: int = 25       # outer frame vs middle (grey levels): bigger = a photo with a background
    photo_border_texture: int = 25        # ... or a frame this busy (grey-level spread) where a scan's margin is flat

    # --- pipeline / jurisdiction packages ---
    ig_package: str = "nrces.fhir.r4.ndhm#6.5.0"
    terminology_package: str = "in-snomed-loinc-icd10"

    # --- web app (upload UI + FHIR API) ---
    webapp_port: int = 8080   # RunPod proxies external 8081 -> localhost:8080
    # Deployment surface. Default = one admin who uploads images and reads the result JSON.
    # The review / reviewer / correction screens and every FHIR path stay shut until the owner
    # switches them on (webapp/surface.py answers 404 for them; the FHIR builder agent is not
    # started and nothing is queued for it).
    review_ui_enabled: bool = False
    fhir_enabled: bool = False
    # HTTP Basic sign-in in front of the whole web app (except /healthz). Blank = no sign-in, which
    # is accepted only while CDI_ENV=dev: any other environment refuses to start without a password.
    admin_user: str = "admin"
    admin_password: str = ""

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
    # where TrOCR runs: cpu | cuda | auto (cuda when a GPU is visible). The pod puts it on the same
    # GPU as Qwen (about 1.3 GB); a missing GPU with "cuda" falls back to the CPU and says so.
    trocr_device: str = "cpu"
    # RapidOCR on the GPU needs onnxruntime-gpu with a CUDA build that supports the card; if the
    # CUDA provider cannot start it falls back to the CPU and logs which one it is using.
    rapidocr_use_cuda: bool = False
    # crop standard (IM-S3): what every handwriting crop looks like when it reaches a reader.
    # PLACEHOLDERS, not tuned: accuracy by crop size is not measured yet (the size of every crop is
    # recorded with the line so it can be). 0 turns the upscaling off.
    crop_pad_frac: float = 0.04           # padding on each side, as a share of the box, so strokes are not cut
    crop_pad_min_px: int = 4              # ... and never less than this
    crop_min_height_px: int = 32          # a crop shorter than this is upscaled before it is read
    crop_max_upscale: float = 4.0         # ... by at most this factor; still shorter = flagged below_standard
    # self-consistency (RD-S3): read each handwriting crop a second time with different padding; a
    # mismatch between the two Qwen readings is another disagreement signal. OFF by default: it
    # doubles the Qwen calls per line (latency and GPU cost are not measured yet).
    qwen_self_consistency: bool = False
    self_consistency_pad_frac: float = 0.12
    # disagreement-rate drift alarm: needs this many earlier runs, then alarms when the share of
    # disagreeing lines moves by more than max(sigma x the usual spread, the absolute tolerance)
    drift_min_runs: int = 20
    drift_sigma: float = 3.0
    drift_abs_tolerance: float = 0.15
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
    # Text too small to read reliably (IM-S1): the p85 height of a glyph in pixels, measured on the page
    # as sent to the readers. PLACEHOLDER, not tuned on real photos or handwriting. Basis: on clean
    # SYNTHETIC printed text RapidOCR read >= 97% of characters down to a 8 px font (glyph p85 ~ 6 px),
    # so 8 only rejects pictures far below that; handwriting very likely needs more. 0 turns the check off.
    quality_min_text_height_px: int = 8
    # a page looks sideways when the vertical-line score exceeds the horizontal-line score by this
    # factor (synthetic pages: upright 0.08-0.31, turned 3.2-12; tune on real pictures)
    quality_sideways_ratio: float = 2.0
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
