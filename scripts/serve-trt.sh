#!/usr/bin/env bash
# Serve with TensorRT-LLM (trtllm-serve, OpenAI-compatible) in the NGC container. Offline: model must be in the HF cache.
# Runs in the foreground so systemd can supervise it; `docker stop trtllm-edge` stops it.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${MODEL:-nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4}"
IMAGE="${IMAGE:-nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13}"
YAML="${YAML:-$PWD/trt/nano.yaml}"
NAME="${NAME:-trtllm-edge}"
docker rm -f "$NAME" >/dev/null 2>&1 || true
exec docker run --rm --name "$NAME" --gpus all --ipc host --network host \
  -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" -e PYTORCH_ALLOC_CONF=expandable_segments:True \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" -v "$YAML:/config.yaml:ro" \
  "$IMAGE" trtllm-serve serve "$MODEL" \
    --host "${HOST:-127.0.0.1}" --port "${PORT:-8355}" \
    --trust_remote_code \
    --reasoning_parser "${REASONING_PARSER:-nano-v3}" --tool_parser "${TOOL_PARSER:-qwen3_coder}" \
    --extra_llm_api_options /config.yaml "$@"
