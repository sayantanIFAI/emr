# Running CDI-Adapter on RunPod (native, no Docker)

This pod: **RTX PRO 4000 Blackwell 24 GB**, Ubuntu 24.04, torch 2.8+cu128, **no Docker**,
`/` overlay = 30 GB (wiped on restart), `/workspace` = MooseFS (persistent).

### Why not the compose stack / why Postgres is on the overlay

`/workspace` (MooseFS) **cannot host a POSIX-permissions filesystem** here: no loop
devices, no `/dev/fuse`, no `CAP_SYS_ADMIN`, and directories are forced to `0777` — which
Postgres rejects for `PGDATA`. So:

| On `/workspace` (persistent) | On `/` overlay (rebuilt each boot) |
|---|---|
| repo `/workspace/cdi`, `.venv`, sample data | apt packages (postgres, redis clients) |
| **MinIO object store `/workspace/minio-data`** — the real scanned-document bytes | the live Postgres **cluster** (`/var/lib/postgresql/16/cdi`) |
| models, `HF_HOME=/workspace/hf-cache` | |
| **`/workspace/backup/cdi.dump`** — periodic `pg_dump` of the derived rows | |

Nothing irreplaceable is lost on restart: document bytes live in MinIO on `/workspace`, and
the Postgres rows are both reproducible from those bytes *and* dumped to `/workspace`.

## First time

```bash
cd /workspace/cdi
cp .env.runpod .env
bash infra/runpod/bootstrap_pod.sh      # installs+starts pg/redis/minio, venv, migrations
```

## After every pod restart

```bash
bash /workspace/cdi/infra/runpod/bootstrap_pod.sh
```

It reinstalls the apt bits, restarts the three services, `pg_restore`s
`/workspace/backup/cdi.dump` if present, then `alembic upgrade head`.

## Periodic DB snapshot

```bash
bash /workspace/cdi/infra/runpod/snapshot.sh
# or every 15 min:
(crontab -l 2>/dev/null; echo "*/15 * * * * bash /workspace/cdi/infra/runpod/snapshot.sh") | crontab -
```

## Smoke test (ingest slice — CPU only, no GPU needed)

```bash
cd /workspace/cdi && . .venv/bin/activate
python scripts/make_sample_docs.py --out /workspace/data/inbox --count 3
python -c "from cdi_adapter.ingest.watcher import scan_once; print('ingested', scan_once())"

psql "$CDI_DATABASE_URL" -c "SELECT status,count(*) FROM source_document GROUP BY 1;"
psql "$CDI_DATABASE_URL" -c "SELECT page_no,width_px,height_px,preproc->>'skew_deg' skew FROM document_page ORDER BY 1 LIMIT 10;"
/workspace/bin/mc ls -r local/cdi-documents | head
```

## GPU serving (Phase 1 remainder onward) — 24 GB budget

Full-precision 7B VLM + 7B DSLM will **not** co-reside in 24 GB. Use one of:

- **AWQ / GPTQ 4-bit**: `Qwen2.5-VL-7B-Instruct-AWQ` (~7 GB) + `Qwen2.5-7B-Instruct-AWQ` (~5 GB)
  + SapBERT (~0.5 GB) — fits with ~16k context, load both via vLLM `--enable-lora`.
- **3B tier**: `Qwen2.5-VL-3B-Instruct` for classify+OCR, 3B DSLM — full precision, roomy.
- **One-at-a-time**: single vLLM process, hot-swap model per pipeline stage.

```bash
pip install vllm            # torch 2.8/cu128 wheels support Blackwell sm_120
HF_HOME=/workspace/hf-cache python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-VL-7B-Instruct-AWQ --quantization awq_marlin \
  --max-model-len 16384 --gpu-memory-utilization 0.55 --port 8000
```
