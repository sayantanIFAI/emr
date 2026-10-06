# Upload screen (epic UP, story UP-S1)

What a person can do on `/`, and what the server enforces. The browser checks the same limits
first, but the server never trusts it.

## Behaviour

- Add pages three ways: **Take a picture** (`<input capture>`: the camera on a phone, a file chooser
  on a laptop), **Choose files**, or drag and drop. Each page gets a preview (images), its size, and
  buttons to move it up / down or remove it; "add another page" is the same two buttons again.
- **One prescription or separate files.** With two or more pictures the person chooses
  `one_document` (default: the pages of ONE prescription, one document, one result) or `separate`
  (each file is read on its own: the behaviour before this story). A PDF cannot be mixed into one
  prescription (it already holds its pages), so with a PDF present the choice is `separate`.
- Accepts JPG, PNG, TIFF and PDF only, decided from the file's **bytes**, not its name.
- Press Send once: the button and the page controls are disabled while sending, and the request
  carries an `Idempotency-Key`, so a double tap or a retry after a dropped connection returns the
  SAME job (`webapp/jobs.py`, in memory, one hour, per process).
- A picture whose short side is below `CDI_QUALITY_MIN_SHORT_SIDE_PX` (600) gets a warning before
  sending: the server holds such a picture for a retake anyway.

## API

| | |
|---|---|
| `GET /api/upload/limits` | `accept`, `max_files`, `max_file_mb`, `max_total_mb`, `min_short_side_px` (from settings) |
| `POST /api/jobs` | form: `files` (1..N), `grouping` (`separate` default / `one_document`), optional `abha`, `patient_ref`; header `Idempotency-Key`. Response unchanged: `{job_id, documents}` (`documents` = 1 for `one_document`) |

Every refusal is `{"detail": "<one plain sentence>"}` (422, or 413 for size).

Settings (all `CDI_`): `UPLOAD_MAX_FILES` (10), `UPLOAD_MAX_FILE_BYTES` (50 MB), `UPLOAD_MAX_TOTAL_BYTES`
(150 MB), `UPLOAD_MIME_TYPES`. The three limits are PLACEHOLDERS, not measured: the whole upload is
read into memory (at most limit + 1 bytes per file), so they bound memory per request.

Free text: `abha` must be 14 digits (spaces / dashes allowed, stored as `XX-XXXX-XXXX-XXXX`);
`patient_ref` is at most 40 characters of letters, digits, space and `- _ . / @`.

## Pictures are never made worse

- The browser sends each `File` exactly as chosen: nothing is resized or re-compressed there.
- `ingest/assemble.py` builds the one-prescription PDF: a JPEG is embedded **byte for byte**
  (`DCTDecode`, EXIF rotation carried as the page's `/Rotate`); a PNG / TIFF frame / CMYK or mirrored
  JPEG is decoded once and stored losslessly (`FlateDecode`; transparency is laid on white). Each
  page is sized so rendering at `CDI_PAGE_DPI` returns the picture's own pixels
  (MEASURED: mean absolute pixel difference 0.0 for RGB JPEG, grey JPEG, PNG and an EXIF-rotated
  JPEG, `tests/test_assemble_unit.py`). The output is deterministic, so duplicate detection works.
- Every original upload is also stored untouched at
  `documents/<sha[:2]>/<sha>/parts/NN.<ext>`, and their SHA-256 hashes are audited
  (`source_document` update, `original_parts`). The assembled PDF is a derived container, never the
  only copy.

## UP-S3 as built: a JSON placeholder connector, shown on the screen

The real downstream (HIS / EMR) is not chosen, so UP-S3 is a connector interface with one
implementation, `json_placeholder` (`output/json_connector.py`, setting `CDI_OUTPUT_CONNECTOR`).
When a job finishes the screen shows the connector's JSON for each document, with a **Download JSON**
button that serves the same bytes (`GET /api/documents/{id}/result.json`, `?download=true`;
`GET /api/jobs/{id}/result.json` for the whole job). Rules the JSON keeps: every value is
`{value, status, reason, confidence}`; status is `accepted` / `needs_check` / `rejected`, or
`not_gated` for identity and doctor fields read from the page; the document is `needs_check` while
any value is; unknown is `null`; what is not extracted yet is listed in `not_extracted`; the
same document gives the same bytes. The notice wording is a PLACEHOLDER pending owner and clinician
approval. The four result cards, click-to-highlight evidence and "Report a wrong result" are not
built (the real contract OUT-S2 replaces this connector).

## Not part of this story (still open)

- Per-page quality reasons in plain words, "Retake this page", replace-one-page endpoint (UP-S2).
- Extraction still reads page 1's image for the model call; pages 2..N reach it as OCR text only
  (`extract/service.py`). That is existing behaviour for any multi-page document.
