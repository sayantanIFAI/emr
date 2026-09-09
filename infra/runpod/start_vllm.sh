#!/usr/bin/env bash
# Opt-in: start a `vllm serve` process for the model gateway's `vllm` backend.
#
#   bash /workspace/cdi/infra/runpod/start_vllm.sh
#
# Runs in ITS OWN venv (/workspace/vllm-venv) so vLLM's torch/deps never touch
# the app venv. The mlserve `hf` backend must be stopped first - only one process
# can hold the model. Rollback = stop this, set CDI_MLSERVE_BACKEND=hf, restart
# mlserve.
set -uo pipefail

VENV=/workspace/vllm-venv
LOG=/workspace/logs/vllm.log
PORT="${CDI_VLLM_PORT:-8078}"
MODEL="${CDI_VLM_MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"
export HF_HOME=/workspace/hf-cache PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=INFO
# Blackwell (sm_120): vLLM 0.28's FlashInfer sampler misfires a stale CUDA-version
# check and aborts engine init - use the native sampler + FlashAttention.
export VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN
mkdir -p /workspace/logs

echo "########## 0. free the GPU (stop the hf gateway) ##########"
pkill -f cdi_adapter.mlserve 2>/dev/null || true
sleep 3
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader || true

echo "########## 1. venv + vLLM ##########"
if [ ! -x "$VENV/bin/vllm" ]; then
  export TMPDIR=/workspace/tmp PIP_CACHE_DIR=/workspace/tmp/pipcache
  mkdir -p "$TMPDIR"
  python3 -m venv "$VENV"          # NOT --system-site-packages: vLLM brings its own torch
  "$VENV/bin/pip" install -U pip wheel
  "$VENV/bin/pip" install vllm     # pulls its own pinned torch + CUDA libs
fi
"$VENV/bin/vllm" --version || { echo "vLLM install failed"; exit 1; }

echo "########## 2. serve $MODEL on :$PORT ##########"
pkill -f "vllm serve" 2>/dev/null || true
sleep 2
setsid nohup "$VENV/bin/vllm" serve "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --served-model-name "$MODEL" \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 16384 \
  --max-num-seqs 8 \
  --limit-mm-per-prompt '{"image": 2}' \
  --mm-processor-kwargs '{"max_pixels": 2000000, "min_pixels": 3136}' \
  --enable-prefix-caching \
  > "$LOG" 2>&1 < /dev/null &   # xgrammar is the default structured-output backend
echo "vllm pid $!  (log: $LOG)"

echo "########## 3. wait for readiness (model load is slow) ##########"
for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1 && { echo "vLLM up"; break; }
  sleep 5
done
curl -s "http://127.0.0.1:${PORT}/v1/models" || { echo; echo "NOT READY - tail $LOG:"; tail -n 40 "$LOG"; exit 1; }
echo
echo "next:  set CDI_MLSERVE_BACKEND=vllm in /workspace/cdi/.env  &&  restart mlserve + webapp"
