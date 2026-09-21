#!/usr/bin/env bash
# Throwaway experiment: start a server with MODEL + extra vllm flags on PORT (default 8101), wait for health,
# run bench.py + smoke.py, stop it. Server log: logs/try-<TAG>.log. Does not touch the systemd service.
# Usage: TAG=27b-kvfp8 scripts/try.sh unsloth/Qwen3.8-27B-NVFP4 --kv-cache-dtype fp8 \
#          --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3
set -uo pipefail
cd "$(dirname "$0")/.."
MODEL="$1"; shift
TAG="${TAG:-$(basename "$MODEL")}"; PORT="${PORT:-8101}"; LOG="logs/try-$TAG.log"
source .venv/bin/activate
# FlashInfer JIT-compiles kernels with nvcc; vLLM marks FlashInfer "unavailable" if nvcc is not on PATH.
[ -d /usr/local/cuda/bin ] && export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}" PATH="/usr/local/cuda/bin:$PATH"
# Unified memory: ninja would fan out ~20 nvcc processes during FlashInfer JIT and OOM the box next to the loaded weights.
export MAX_JOBS="${MAX_JOBS:-4}" FLASHINFER_NVCC_THREADS="${FLASHINFER_NVCC_THREADS:-4}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
vllm serve "$MODEL" --served-model-name edge-agent --host 127.0.0.1 --port "$PORT" \
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.5}" --max-model-len "${MAX_MODEL_LEN:-32768}" "$@" > "$LOG" 2>&1 &
PID=$!
t0=$(date +%s)
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "[$TAG] SERVER DIED after $(( $(date +%s) - t0 ))s"; grep -E "fatal error|Error|error:|Traceback" "$LOG" | grep -v '\]  ' | tail -6 | cut -c1-220; exit 1
  fi
  sleep 5
done
echo "[$TAG] healthy after $(( $(date +%s) - t0 ))s | $(grep -oE 'GPU KV cache size: [0-9,]+ tokens' "$LOG" | tail -1) | $(grep -oE 'Model loading took [0-9.]+ GiB' "$LOG" | tail -1)"
VLLM_URL="http://127.0.0.1:$PORT" ${RUN:-python scripts/bench.py}   # RUN="python scripts/probe.py" swaps the workload
VLLM_URL="http://127.0.0.1:$PORT" python scripts/smoke.py 2>&1 | tail -2
kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
echo "[$TAG] done"
