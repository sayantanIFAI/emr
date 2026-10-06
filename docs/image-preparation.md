# Image preparation (epic IM)

Status of each story against the code, with what was measured. Numbers are labelled MEASURED
(run here, synthetic pages, local Windows PC, OpenCV 5.0.0) or PLACEHOLDER (a setting that must be
tuned on real pictures). Nothing here was measured on real prescription photos.

## IM-S1: check every picture first (`recognition/quality.py`)

Built before this change: blur, glare, darkness, ink, size and a sideways flag; holds with a text
reason; every measurement stored with the page (`document_page.preproc.quality`); limits are
settings. Added with this change:

- **Stable reason codes.** `QualityReport.reason_codes` / `warning_codes`, one per message:
  `resolution_low`, `blurred`, `glare`, `too_dark`, `text_too_small` (holds) and `blank_page`,
  `sideways_suspected` (warnings). The upload screen (UP-S2) maps each code to one plain sentence.
- **Text height, not file size.** `text_height_px` = the 85th percentile of glyph-blob heights
  (about 0.7 of the font size), measured at full resolution on the three inkiest 1000 px tiles so a
  22 MP photo costs the same as a scan. A page with at least 150 glyph-sized blobs whose text is
  below `CDI_QUALITY_MIN_TEXT_HEIGHT_PX` (default 8, **PLACEHOLDER**) is held `text_too_small`
  ("move closer"). MEASURED: a 4000x5600 px photo with 9 px text is held; the same photo with 60 px
  text passes.
  Why 8: on clean synthetic printed text RapidOCR read at least 97% of characters down to an 8 px font
  (glyph p85 about 6 px), so clean printed text is not the limit; handwriting very likely needs
  more, and **no handwriting was measured**. Tune on the answer-key set.
- **A sideways measure that works.** The old flag compared column and row ink variance over the whole
  page and was fooled by margins (MEASURED: an upright left-aligned page scored 1.55 and a turned one
  0.67, so a 90-degree page passed unflagged). `orientation_ratio` scores the letter-merged line
  profile inside the ink's bounding box. MEASURED on 4 synthetic layouts (full width, narrow column,
  sparse, table): upright 0.08-0.31, turned 90/270 degrees 3.2-12; threshold `CDI_QUALITY_SIDEWAYS_RATIO`
  = 2.0. A noisy photo with a dark background scored about 1.0 (ambiguous: not flagged). It cannot tell
  90 from 270 or 0 from 180 and is only a warning.
- **False-reject report (AC3).** `python -m cdi_adapter.recognition.quality_eval --readable DIR
  --unreadable DIR [--manifest m.csv]` prints false-reject and false-accept counts and rates, split by
  document type from the manifest, with every `CDI_QUALITY_*` threshold used. It only measures; run it
  on the answer-key set when it exists.
- **Tests** (`tests/test_quality_unit.py`, 41): the acceptance criteria, one test per code, the
  settings really are settings, 4 layouts x 90/270 degrees, a synthetic set of 18 readable variants
  (noise, JPEG q60, light blur, 6 degree tilt, grey paper) with none rejected, and odd inputs (blank,
  black, noise, partial strip, thin slice, tiny) that must never crash and always report.

Cost, MEASURED on this PC for a 3406x4750 px page: the check takes 0.84 s (0.37 s before this change;
the story's earlier figure is 0.5 s on the pod). Still CPU only.

Not done in IM-S1: thresholds tuned on real pictures and the false-reject rate on them (needs the
answer-key set); "page cut off" is not detected; a photo with a dark background is not reliably
flagged sideways.

## IM-S2: straighten and crop (`ingest/pages.py`): partly built, not changed here

- Deskew works: MEASURED residual tilt 0.0 degrees after +-3, 6 and 12 degree tilts (`tests/test_quality_unit.py`,
  independent projection-profile measure). Verified only on OpenCV 5.0.0; `pyproject` allows `>=4.10`
  and `minAreaRect`'s angle convention changed between OpenCV versions, so the test must pass on
  whatever version the pod installs.
- Missing: turning a 90 / 270 degree page upright, detecting and fixing 180 degrees, page edges and
  perspective correction, hold-for-retake when the edges cannot be found, a stored transform with an
  inverse mapping (only `skew_deg` and `steps` are stored), and the round-trip test. The normalised
  copy also has denoise + CLAHE applied (the story says no filters that could change letters; the
  handwriting crops are cut from the unfiltered render, RapidOCR reads the filtered one).

## IM-S3: text lines and printed / handwritten labels (`recognition/regions.py`): built, gaps

- Built: line detection, four labels (printed, handwritten, mixed, uncertain), uncertain and mixed go
  to both reader paths, crops cut from the unfiltered render with a hash and the box recorded.
  MEASURED: 33 lines on a synthetic page in 0.2 s (`detect_lines`), 0.27 s with the per-line label.
- **Crop standard (added):** `prepare_crop` cuts each handwriting crop from the unfiltered render with padding
  (`CDI_CROP_PAD_FRAC` 0.04, `CDI_CROP_PAD_MIN_PX` 4, never past the page), enlarges one shorter than
  `CDI_CROP_MIN_HEIGHT_PX` (32, **PLACEHOLDER**) by at most `CDI_CROP_MAX_UPSCALE` (4x, cubic, aspect kept), and
  records the box, padding applied, size cut, scale, size delivered and `below_standard` in the line's
  `ocr_block.recognition.crop`. The crop hash is of what the readers received. `tests/test_crops_unit.py` (15).
- Still missing: accuracy by crop size (needs real data), the ablation of the routing step, and recall / label
  accuracy on real handwriting.
- The crops are cut from the deskewed colour render, not from the original upload.
