#!/usr/bin/env bash
# Start the vLLM OpenAI-compatible server for the edge agent.
# Offline by default: the model must already be in the HF cache (run scripts/download.sh once while online).
# Defaults picked from docs/bench-2026-09-22.md (Qwen3.6-35B-A3B-FP8 + MTP: ~67 tok/s single stream on the Spark).
set -euo pipefail
cd "$(dirname "$0")/.."
# Activate instead of calling .venv/bin/vllm directly: FlashInfer's JIT looks for `ninja` on PATH.
source .venv/bin/activate
# FlashInfer JIT-compiles kernels with nvcc; vLLM marks FlashInfer "unavailable" if nvcc is not on PATH.
[ -d /usr/local/cuda/bin ] && export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}" PATH="/usr/local/cuda/bin:$PATH"
# Unified memory: ninja would fan out ~20 nvcc processes during FlashInfer JIT and OOM the box next to the loaded weights.
export MAX_JOBS="${MAX_JOBS:-4}" FLASHINFER_NVCC_THREADS="${FLASHINFER_NVCC_THREADS:-4}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

MODEL="${MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}"
TOOL_PARSER="${TOOL_PARSER:-qwen3_coder}"      # Qwen3-8B needs `hermes`
# In-checkpoint multi-token prediction; set SPEC="" to disable, or pass a full --speculative-config JSON.
DEFAULT_SPEC='{"method":"mtp","num_speculative_tokens":3}'
SPEC="${SPEC-$DEFAULT_SPEC}"
extra=()
[ -n "$SPEC" ] && extra+=(--speculative-config "$SPEC")

exec vllm serve "$MODEL" \
  --served-model-name "${SERVED_NAME:-edge-agent}" \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8100}" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --max-num-seqs "${MAX_NUM_SEQS:-8}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.45}" \
  --trust-remote-code \
  --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER" \
  --reasoning-parser qwen3 \
  "${extra[@]}" \
  "$@"
