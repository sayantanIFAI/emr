# Reminders and open decisions

Things deliberately left for later, with what is known today. Newest first. Delete an item when it is done.

## 0. Reader model: the 32B was tried and NOT adopted (2026-10-07)

`Qwen/Qwen2.5-VL-32B-Instruct-AWQ` (Apache-2.0, commercial use allowed, 20.7 GB, registered in `compliance/models.json` as a
**candidate** with its exact revision and file checksums) was swapped in on the RTX 6000 Ada with vLLM and measured against
the 7B on the same three real handwritten pages:

| page | 7B fp8 | 32B AWQ |
|---|---|---|
| Apollo OPD note | 14 s | 42 s |
| Apollo Sugar prescription | 20 s | 50 s |
| vet clinic sheet (circled test line) | 18-25 s | 33-42 s |

No clear accuracy gain: the 32B gave a more consistent medicine list, but misread other fields differently
(`fever` -> `future`), and asked directly (no examples in the prompt) about the circled test line it did NOT read it: it
invented a typical list (`Stool Parasite Exam`, `Chest X-ray`) and a wrong next-visit date. Rolled back to the 7B.
`scripts/register_model.py` adds a model to the registry from measured hashes; swapping is a settings change
(`CDI_VLM_MODEL_ID`, `CDI_VLLM_QUANT`, registry champion) plus `infra/runpod/start_vllm.sh`.
**Revisit when:** a faster GPU makes 40 s acceptable, or a handwriting-specific / fine-tuned reader is available. The
first thing to build for accuracy is a labelled evaluation set of real pages (typed ground truth), so every model or
prompt change is measured, not eyeballed.

## 1. Per-doctor learning (later)

**Goal:** each known doctor's way of writing (their abbreviations, their medicine brands, how they list tests) makes the
reading stronger over time.

**What exists:** `recognition/alias.py` has alias classes **A** (verified), **B** (generated) and **C** (this doctor's
verified habit, matched first, only for that doctor), and `corrections/` writes class C aliases when a reviewer corrects a
value. With the review screens off (`CDI_REVIEW_UI_ENABLED=false`) nothing feeds it.

**Rules for the design (decided, do not skip):**
- Never learn from the model's own unverified output: it would teach the system its mistakes.
- A doctor alias may be created only from (a) a reviewer's correction, or (b) agreement of independent signals: the
  deterministic gate resolved it exactly (Common Lab Codes / Common Drug Codes / the curated table) on at least N documents
  of the same doctor, with no disagreement between the readers.
- Key a doctor by registration number (`prescriber.reg_no`) + clinic, not by name; two doctors can share a name.
- Per-doctor patterns to capture: how they list tests (`Adv`, `Ix`, `F/U with ...`), how they write a duration (`x 5d`),
  their abbreviations, the order of their lines. Store them as aliases / line-shape counts, not as free text.
- Add an evaluation set per doctor first (20 pages with typed ground truth), so "stronger" is measured.
- Fine-tuning a reader per doctor needs consent / data-protection review (DPDP) and the licence of the base model.

## 2. Licence of the Indian code sets (PoC now, production later)

The Common Lab Codes for India (LOINC subset), the Common Drug Codes for India (SNOMED CT national extension) and the Drug
Information Service Bundle are C-DAC / NRCeS packages whose licence allows redistribution **within India**. The PoC pod is
in a US data centre (RunPod US-WA-1); the owner accepted this for PoC / research / evaluation (2026-10-07).
**Before production:** host in India (or get the terms checked), keep the packages and the built indexes out of git, and
keep `CDI_LICENSED_CODE_SYSTEMS` listing only the systems the customer is licensed for.

## 3. Persistence of the pod

`/workspace` on the PoC pod is the pod's own container disk, not a volume: a stop, reset or delete loses the models, the
database, the stored scans and the passwords. `start_all.sh` prints a warning. Until a network volume is mounted at
`/workspace`: `bash /workspace/cdi/infra/runpod/prepare_stop.sh` before stopping, a pack refreshes every 30 minutes at
`/workspace/offpod/cdi-state.tar.gz`, and `infra/runpod/restore_pack.sh` restores it. The Indian-code indexes and the
medicine-name list live in `/workspace/data/` and are part of that pack.

## 4. Known failing tests (not caused by recent work)

- `test_compliance_unit::...installed_environment_passes_the_gate`: the licence audit trips on system Python packages.
- `test_pdf_library_guard_unit::...`: the guard finds the word "pymupdf" in the repo's own comments and tests.
- `test_pdf_render_unit::...fast_compression_level`: depends on the installed Pillow version.
- `test_ingest_integration::test_ingest_creates_rows_and_pages`: re-ingests identical bytes, fails on the second run.
- The idempotency tests write fixed keys (`send-1`, `send-2`, `same`, `k`, `old`) into the live database and fail on the
  next run until they are deleted. They should use a throw-away database.

## 5. Reading quality (what is still hard)

Crowded, slanted handwriting on a small photo (a vet clinic sheet with a circled `CBC / KFT / LFT` line) is not read by
the 7B model; nothing downstream can create a test that was never seen. A stronger reader is the lever (see the model
registry, `compliance/models.json`); the deterministic gate (`extract/lab_resolve.py`, `indian_codes.py`,
`lab_gazetteer.py`, `medicine_lexicon.py`) only stops wrong things being shown, and the model may only choose among
reference names (`extract/resolve_llm.py`).
