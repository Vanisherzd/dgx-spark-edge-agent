#!/usr/bin/env bash
# Serve with TensorRT-LLM (trtllm-serve, OpenAI-compatible) in the NGC container. Offline: model must be in the HF cache.
# Runs in the foreground so systemd can supervise it; `docker stop trtllm-edge` stops it.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4}"   # text-only; the Omni checkpoint trips a tokenizer bug in 1.3.0rc13
IMAGE="${IMAGE:-nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13}"
YAML="${YAML:-$PWD/trt/nano.yaml}"
NAME="${NAME:-trtllm-edge}"
# Offline: trtllm-serve resolves the tokenizer through the HF API unless given a local path, so hand it the cached
# snapshot directory (same path inside the container, the cache is bind-mounted 1:1 under /root).
SNAP=$(ls -d "$HOME/.cache/huggingface/hub/models--${MODEL//\//--}/snapshots/"*/ 2>/dev/null | head -1 || true)
[ -n "$SNAP" ] || { echo "model $MODEL not in the HF cache; run: uv run hf download $MODEL" >&2; exit 1; }
TOK_IN_CONTAINER="/root/.cache/huggingface/hub/${SNAP#"$HOME/.cache/huggingface/hub/"}"
# Already serving? Do not kill a healthy server by accident (pass FORCE=1 to restart anyway).
if [ "${FORCE:-0}" != "1" ] && docker ps --format '{{.Names}}' | grep -qx "$NAME" && curl -sf "http://127.0.0.1:${PORT:-8000}/health" >/dev/null 2>&1; then
  echo "$NAME is already running and healthy on port ${PORT:-8000}; FORCE=1 to restart" >&2; exit 0
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
exec docker run --rm --name "$NAME" --gpus all --ipc host --network host \
  -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" -e PYTORCH_ALLOC_CONF=expandable_segments:True -e TRTLLM_ENABLE_PDL="${TRTLLM_ENABLE_PDL:-1}" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" -v "$YAML:/config.yaml:ro" \
  "$IMAGE" trtllm-serve serve "$MODEL" \
    --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --served_model_name "${SERVED_NAME:-edge-agent}" \
    --trust_remote_code --tokenizer "$TOK_IN_CONTAINER" \
    --reasoning_parser "${REASONING_PARSER:-nano-v3}" --tool_parser "${TOOL_PARSER:-qwen3_coder}" \
    --extra_llm_api_options /config.yaml "$@"
