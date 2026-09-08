#!/usr/bin/env bash
# Re-establish the CDI-Adapter dev stack after a RunPod restart.
#
# Persistence model on this pod:
#   /workspace  = MooseFS, persistent, but CANNOT hold a POSIX-perms filesystem
#                 (no loop devices, no /dev/fuse, no CAP_SYS_ADMIN -> dirs are forced 0777,
#                  which Postgres refuses for PGDATA).
#   Therefore:
#     - repo, venv, sample data, MODELS, HF cache, MinIO object store (the real
#       document bytes), and the Postgres DUMP all live on /workspace  -> nothing lost.
#     - the live Postgres cluster runs on the ephemeral overlay and is REBUILT here
#       on every start, then restored from /workspace/backup/cdi.dump if present.
#
# Usage:  bash /workspace/cdi/infra/runpod/bootstrap_pod.sh
set -euo pipefail

WS=/workspace
REPO=$WS/cdi
PGBIN=/usr/lib/postgresql/16/bin
PGDATA=/var/lib/postgresql/16/cdi
export DEBIAN_FRONTEND=noninteractive

echo "== 1/6 apt packages (ephemeral, reinstalled each boot) =="
if ! command -v psql >/dev/null || ! command -v redis-server >/dev/null || ! command -v crontab >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq postgresql postgresql-contrib redis-server cron curl ca-certificates \
                        libgl1 libglib2.0-0 >/dev/null
fi

echo "== 2/6 Postgres cluster on overlay =="
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  install -d -o postgres -g postgres -m 700 "$PGDATA"
  sudo -u postgres "$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust --encoding=UTF8 >/tmp/initdb.log 2>&1
fi
if ! sudo -u postgres "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
  sudo -u postgres "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
    -o "-k /tmp -c listen_addresses=127.0.0.1 -p 5432" start
  sleep 3
fi
sudo -u postgres "$PGBIN/psql" -h 127.0.0.1 -tc \
  "SELECT 1 FROM pg_roles WHERE rolname='cdi'" | grep -q 1 || \
  sudo -u postgres "$PGBIN/psql" -h 127.0.0.1 -c "CREATE ROLE cdi LOGIN PASSWORD 'cdi' SUPERUSER;"
sudo -u postgres "$PGBIN/psql" -h 127.0.0.1 -tc \
  "SELECT 1 FROM pg_database WHERE datname='cdi'" | grep -q 1 || \
  sudo -u postgres "$PGBIN/createdb" -h 127.0.0.1 -O cdi cdi

echo "== 3/6 Redis (data on /workspace) =="
redis-cli -p 6379 ping >/dev/null 2>&1 || \
  redis-server --daemonize yes --dir "$WS/redis" --appendonly yes \
    --port 6379 --bind 127.0.0.1 --pidfile "$WS/redis/redis.pid"

echo "== 4/6 MinIO (object store on /workspace) =="
if ! curl -sf http://127.0.0.1:9000/minio/health/ready >/dev/null 2>&1; then
  [ -x "$WS/bin/minio" ] || { curl -sSL https://dl.min.io/server/minio/release/linux-amd64/minio -o "$WS/bin/minio"; chmod +x "$WS/bin/minio"; }
  [ -x "$WS/bin/mc" ]    || { curl -sSL https://dl.min.io/client/mc/release/linux-amd64/mc      -o "$WS/bin/mc";    chmod +x "$WS/bin/mc"; }
  MINIO_ROOT_USER=cdiadmin MINIO_ROOT_PASSWORD=cdiadminsecret \
    nohup "$WS/bin/minio" server "$WS/minio-data" \
      --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 >"$WS/minio.log" 2>&1 &
  sleep 4
fi
"$WS/bin/mc" alias set local http://127.0.0.1:9000 cdiadmin cdiadminsecret >/dev/null 2>&1 || true
"$WS/bin/mc" mb --ignore-existing local/cdi-documents >/dev/null 2>&1 || true

echo "== 5/6 venv + schema =="
cd "$REPO"
# --system-site-packages: inherit the RunPod image's torch/torchvision (Blackwell-ready)
[ -d .venv ] || python3 -m venv --system-site-packages .venv
. .venv/bin/activate
export PIP_ROOT_USER_ACTION=ignore
python -c "import cdi_adapter, rapidocr_onnxruntime, transformers" 2>/dev/null || {
  pip install -q -e ".[dev,ocr]"
  pip install -q transformers accelerate qwen-vl-utils einops sentencepiece
}
set -a; . "$REPO/.env"; set +a
PSQL_URL="${CDI_DATABASE_URL/+psycopg/}"   # psql/pg_restore want plain postgresql://
HAS_SCHEMA=$(psql "$PSQL_URL" -tAc "SELECT to_regclass('public.source_document') IS NOT NULL" 2>/dev/null || echo f)
if [ -s "$WS/backup/cdi.dump" ] && [ "$HAS_SCHEMA" != "t" ]; then
  echo "   fresh cluster -> restoring /workspace/backup/cdi.dump"
  pg_restore -d "$PSQL_URL" --no-owner --clean --if-exists "$WS/backup/cdi.dump" 2>/tmp/restore.log || true
fi
alembic upgrade head

echo "== 5b/6 periodic DB snapshot to /workspace =="
( crontab -l 2>/dev/null | grep -v snapshot.sh; \
  echo "*/15 * * * * bash $REPO/infra/runpod/snapshot.sh >> $WS/backup/snapshot.log 2>&1" ) | crontab -
service cron start >/dev/null 2>&1 || true

echo "== 6/6 health =="
export HF_HOME="$WS/hf-cache"
python - <<'PY'
from cdi_adapter.db import ping as dbp
from cdi_adapter.storage import ping as s3p, ensure_bucket
ensure_bucket()
print("db:", dbp(), " s3:", s3p())
PY
echo
echo "Ready. Start services:"
echo "  cd $REPO && . .venv/bin/activate"
echo "  uvicorn cdi_adapter.api:app --host 0.0.0.0 --port 8080 &"
echo "  celery -A cdi_adapter.worker.celery_app worker -Q cdi -l info &"
echo "  python -m cdi_adapter.ingest.watcher &"
