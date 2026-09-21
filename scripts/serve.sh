#!/usr/bin/env bash
# Start the vLLM OpenAI-compatible server for the edge agent.
# Offline by default: the model must already be in the HF cache (run scripts/download.sh once while online).
set -euo pipefail
cd "$(dirname "$0")/.."
# Activate instead of calling .venv/bin/vllm directly: FlashInfer's JIT looks for `ninja` on PATH.
source .venv/bin/activate
# FlashInfer JIT-compiles kernels with nvcc; vLLM marks FlashInfer "unavailable" if nvcc is not on PATH.
[ -d /usr/local/cuda/bin ] && export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}" PATH="/usr/local/cuda/bin:$PATH"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
exec vllm serve "${MODEL:-Qwen/Qwen3-8B}" \
  --served-model-name "${SERVED_NAME:-edge-agent}" \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8100}" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.5}" \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3 \
  "$@"
