# File listener (E16) — OneDrive / SharePoint / Google Drive / local → pipeline

Prescriptions dropped in a cloud folder are picked up, run through the full pipeline
(ingest → OpenCV quality → RapidOCR → classify → recognition v2 → Qwen extract → terminology →
validate) and filed by outcome. **The drive is chosen by configuration only.**

```
<root>/                      CDI_LISTENER_ROOT   (e.g. "OCR")
├── (inbox)   drop prescriptions here        CDI_LISTENER_INBOX   "inbox"  or "." = the root itself
├── processing/   a file being worked on     CDI_LISTENER_PROCESSING
├── success/      pipeline result durably recorded     CDI_LISTENER_COMPLETED
├── error/        failed; retried up to 3 times (unless it is a data error)   CDI_LISTENER_ERROR
├── quarantine/   retries exhausted - needs a person                            CDI_LISTENER_QUARANTINE
└── log/          failure reasons ONLY (one small file per failed run)          CDI_LISTENER_LOG
```

## Behaviour

| Rule | Detail |
|---|---|
| Stable files only | a file is picked up after its size/etag is unchanged for `CDI_LISTENER_STABLE_POLLS` polls (no half uploads); `~$*`, `*.part`, `*.tmp` and non-prescription formats are ignored |
| **Batch of 3** | the listener takes `CDI_LISTENER_BATCH_SIZE` (3) stable files together, oldest first, and runs them **concurrently**; the next batch starts when the whole batch finished. A short batch is released after `CDI_LISTENER_BATCH_WAIT_SECONDS` (60; `0` = never wait; very large = strict "always 3") |
| `success/` | the file moves only after the pipeline result is durably recorded (DB) |
| `error/` + retries | any failure moves the file to `error/`; the **recovery agent** retries it **3 times** with back-off (60 s, 120 s, 240 s = `CDI_LISTENER_RETRY_BASE_SECONDS` × 2ⁿ), so a file runs at most 4 times. The cap is also a DB `CHECK`. After the 3rd failed retry it moves to `quarantine/` |
| Data errors | unreadable / too blurred / too big / empty files are **not** retried (retrying identical bytes cannot help): they stay in `error/` with the reason in `log/` — rescan and drop again |
| `log/` | one note per failed run: `<file>.<UTC time>.run<N>.log` with reason, error class, batch id, run number, what happens next, traceback tail. **Nothing is written for successes** and no notes sit next to the files |
| Safety | one lease per file (a crashed worker's file is recovered); same bytes dedupe on sha256; every transition is in `listener_file.history`; nothing is ever deleted; SIGTERM drains the running batch then exits |

## Run

```bash
python -m cdi_adapter.listener.service --check    # authenticate, create/resolve the folders, show what waits
python -m cdi_adapter.listener.service --login    # OneDrive device-code sign-in (once)
python -m cdi_adapter.listener.service            # the listener loop
python -m cdi_adapter.listener.recovery           # the retry agent (start_all.sh already starts it)
CDI_START_LISTENER=1 bash infra/runpod/start_all.sh   # start_all.sh also starts the listener
```

## Switching drives = changing `.env` only

| | OneDrive / SharePoint | Google Drive |
|---|---|---|
| `CDI_LISTENER_CONNECTOR` | `onedrive` / `sharepoint` | `gdrive` |
| root | `CDI_LISTENER_ROOT=OCR` (folder path in the drive) | `CDI_GDRIVE_ROOT_FOLDER_ID=<folder id>` *or* `CDI_LISTENER_ROOT=OCR` |
| auth | `CDI_GRAPH_*` | `CDI_GDRIVE_*` |
| extra install | `pip install '.[listener]'` | `pip install '.[gdrive]'` |

Everything else (batching, folders, retries, log, pipeline) is identical. A connector that does not
exist yet can be plugged in without touching this code: `CDI_LISTENER_CONNECTOR=my_pkg.module:MyConnector`
(a class with a no-argument constructor implementing `ensure_folders/list/download/move/write_note`).

## OneDrive for Business (Microsoft Graph)

Example folder: `…/personal/<owner>/Documents/OCR` (the owner's OneDrive; the account's e-mail cannot be read back from
that URL because `.` and `@` both become `_`, which is why Option A below needs no e-mail at all) →
`CDI_LISTENER_ROOT=OCR`, `CDI_LISTENER_INBOX=.` (prescriptions go straight into `OCR`; the other
folders are created beside them).

### Option A — sign in once as the person (no admin consent) — `CDI_GRAPH_AUTH=device_code`

1. Entra admin center → *App registrations* → *New registration* (single tenant). *Authentication* →
   **Allow public client flows = Yes**. *API permissions* → Microsoft Graph → **Delegated** →
   `Files.ReadWrite`. Copy the *Application (client) ID* and *Directory (tenant) ID*.
2. `.env`:
   ```
   CDI_LISTENER_CONNECTOR=onedrive
   CDI_LISTENER_ROOT=OCR
   CDI_LISTENER_INBOX=.
   CDI_GRAPH_AUTH=device_code
   CDI_GRAPH_TENANT_ID=<tenant id>
   CDI_GRAPH_CLIENT_ID=<client id>
   CDI_GRAPH_TOKEN_CACHE=/workspace/.cdi/graph_token_cache.json   # persistent disk
   ```
3. `python -m cdi_adapter.listener.service --login` → open the printed URL, enter the code, sign in.
   The password is never seen by this process; the refresh token is cached and renewed silently.
4. `python -m cdi_adapter.listener.service --check` → then start the listener.

### Option B — app-only (service identity, needs a tenant admin) — `CDI_GRAPH_AUTH=app`

App registration → **Application** permission `Files.ReadWrite.All` (OneDrive) or `Sites.Selected`
(SharePoint) → admin consent → certificate (`CDI_GRAPH_CERT_PATH`/`_THUMBPRINT`) or secret
(`CDI_GRAPH_CLIENT_SECRET`); `CDI_GRAPH_USER_ID=<the owner's user principal name>`
(or `CDI_GRAPH_DRIVE_ID`; SharePoint: `CDI_GRAPH_SITE_ID`).

## Google Drive

1. Google Cloud → create a **service account**, enable the *Drive API*, download its JSON key to the pod.
2. **Share the Drive folder with the service account's e-mail as Editor** (or use a shared drive).
3. `.env`:
   ```
   CDI_LISTENER_CONNECTOR=gdrive
   CDI_GDRIVE_AUTH=service_account
   CDI_GDRIVE_CREDENTIALS_FILE=/workspace/secrets/gdrive-sa.json
   CDI_GDRIVE_ROOT_FOLDER_ID=<id from the folder URL>
   CDI_LISTENER_INBOX=.
   ```
   (OAuth instead: `CDI_GDRIVE_AUTH=oauth` + `CDI_GDRIVE_CLIENT_ID/_CLIENT_SECRET/_REFRESH_TOKEN`.)
4. `pip install '.[gdrive]'`, then `--check`.

## Testing

`tests/test_listener_connectors_unit.py` runs the **real** OneDrive and Google Drive connector classes
against in-memory fakes of both APIs (and the local connector) through one shared contract: lifecycle
moves, name clashes, log notes, "root is the inbox", config-only selection. `tests/test_listener_batch_integration.py`
(Postgres) proves that three files run concurrently, batches of three, 3 retries then quarantine, and
failure notes only in `log/`. Live Graph / Drive calls need real credentials and are verified with `--check`.
