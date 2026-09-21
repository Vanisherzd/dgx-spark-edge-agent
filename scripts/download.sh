#!/usr/bin/env bash
# One-time, online: pull the model into the shared HF cache (~/.cache/huggingface). After this, serve.sh runs offline.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run hf download "${MODEL:-Qwen/Qwen3-8B}"
