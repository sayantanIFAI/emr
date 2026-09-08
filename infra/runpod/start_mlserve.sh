#!/usr/bin/env bash
# Start the model gateway (transformers Qwen2.5-VL) on the pod GPU.
# Model weights cache to /workspace/hf-cache (persistent). First run downloads ~16 GB (7B).
set -euo pipefail
REPO=/workspace/cdi
cd "$REPO"
. .venv/bin/activate
set -a; . .env; set +a
export HF_HOME=/workspace/hf-cache
export CDI_MLSERVE_BACKEND=hf
PORT="${CDI_MLSERVE_PORT:-8077}"

mkdir -p /workspace/logs
pkill -f "cdi_adapter.mlserve" 2>/dev/null || true
sleep 1
setsid nohup python -m cdi_adapter.mlserve > /workspace/logs/mlserve.log 2>&1 < /dev/null &
echo "mlserve pid $!  port $PORT  ->  /workspace/logs/mlserve.log"
echo "waiting for gateway (model loads lazily on first request) ..."
for i in $(seq 1 60); do
  body="$(curl -s "http://127.0.0.1:${PORT}/healthz" || true)"
  if printf '%s' "$body" | grep -q '"configured_backend"'; then
    printf '%s\n' "$body"; exit 0
  fi
  sleep 2
done
echo "gateway did not answer on :${PORT} in 120s; tail log:"; tail -40 /workspace/logs/mlserve.log; exit 1
