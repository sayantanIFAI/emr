#!/usr/bin/env bash
# THE single entrypoint. Run this once per pod boot:
#     bash /workspace/cdi/infra/runpod/start_all.sh
#
# Everything needed lives on /workspace (repo, venv, model cache, MinIO object
# store, Postgres dump). This script re-creates only the ephemeral parts:
#   - apt packages (postgres/redis/cron)        [bootstrap_pod.sh]
#   - the Postgres cluster on the overlay,      [bootstrap_pod.sh]
#     restored from /workspace/backup/cdi.dump
#   - the model gateway (Qwen2.5-VL) process
#   - the web app process
set -uo pipefail
REPO=/workspace/cdi
cd "$REPO"
mkdir -p /workspace/logs

echo "########## 1. infra (bootstrap) ##########"
bash "$REPO/infra/runpod/bootstrap_pod.sh" || echo "(bootstrap returned non-zero; continuing)"

. .venv/bin/activate
set -a; . "$REPO/.env"; set +a
export HF_HOME=/workspace/hf-cache
MLP="${CDI_MLSERVE_PORT:-8077}"
WBP="${CDI_WEBAPP_PORT:-8080}"

echo "########## 2. model gateway  (:$MLP) ##########"
if curl -s "http://127.0.0.1:${MLP}/healthz" | grep -q configured_backend; then
  echo "already up"
else
  pkill -f cdi_adapter.mlserve 2>/dev/null || true; sleep 1
  setsid nohup python -m cdi_adapter.mlserve > /workspace/logs/mlserve.log 2>&1 < /dev/null &
  echo "mlserve pid $!"
  for _ in $(seq 1 45); do
    curl -s "http://127.0.0.1:${MLP}/healthz" | grep -q configured_backend && break
    sleep 2
  done
fi
curl -s "http://127.0.0.1:${MLP}/healthz"; echo

echo "########## 3. web app  (:$WBP) ##########"
pkill -f cdi_adapter.webapp 2>/dev/null || true; sleep 1
setsid nohup python -m cdi_adapter.webapp > /workspace/logs/webapp.log 2>&1 < /dev/null &
echo "webapp pid $!"
for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:${WBP}/healthz" >/dev/null && break
  sleep 1
done
curl -s "http://127.0.0.1:${WBP}/healthz"; echo

POD=$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^RUNPOD_POD_ID=//p')
echo
echo "======================================================================"
echo " LIVE URL:  https://${POD}-8081.proxy.runpod.net"
echo " (RunPod proxies external :8081 -> this pod's localhost:${WBP})"
echo "======================================================================"
