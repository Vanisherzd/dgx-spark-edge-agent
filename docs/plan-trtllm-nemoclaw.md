# Plan: TensorRT-LLM inference + NemoClaw agent layer (drafted 2026-09-22, not executed)

Why: a compliance requirement to serve with TensorRT(-LLM) and to run the agent on NVIDIA NemoClaw. Everything stays
on the Spark (edge, offline-capable); only the serving engine and the agent runtime change. Client code is unaffected
(OpenAI-compatible API either way), so `scripts/bench.py`, `probe.py`, `smoke.py` and `vllm bench serve` still work
against the new endpoint.

## Facts checked
- TensorRT-LLM container `nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13` has an arm64 manifest; TRT-LLM 1.2 added beta
  single-node DGX Spark support. The Spark playbook validates Nemotron-3-Nano-Omni-30B-A3B (BF16/FP8/NVFP4),
  Nemotron-3-Super-120B NVFP4, Qwen3 8B/14B/32B, Qwen3-30B-A3B NVFP4, GPT-OSS 20B/120B MXFP4, Llama 3.x. No speculative
  decoding is documented for Spark.
- Qwen3.6-35B-A3B (gated-delta-net hybrid) is not a TRT-LLM path: not in the release notes through 1.2, NVIDIA's own
  `nvidia/Qwen3.6-35B-A3B-NVFP4` card ships vLLM commands only, and Qwen3.5 support is an open TRT-LLM issue.
- TRT-LLM ≥ 1.0 serves through its PyTorch backend (TensorRT-LLM kernels, no TensorRT engine build). If the rule means a
  literal TensorRT engine, that is not available for these MoE/NVFP4 models on Spark; confirm the wording first.
- NemoClaw = OpenClaw agent inside NVIDIA OpenShell. The gateway is a privileged Docker container embedding k3s; the
  sandbox is a pod in it. Host port 8080 must be published as 8080 (or start the gateway on another port). Any
  OpenAI-compatible endpoint can be the inference provider (`NEMOCLAW_PROVIDER=custom`).
- This Spark already has NemoClaw from 2026-03 (`/usr/bin/nemoclaw`, `~/.local/bin/openshell`, `~/.nemoclaw/sandboxes.json`
  with provider `ollama-local`, model `nemotron-3-super:120b`). It is stale; rerun the installer.
- Blocker: the Spark is a kubelet node of the lab cluster. OpenShell's embedded k3s fights the host kubelet over
  `/sys/fs/cgroup/kubepods` (NemoClaw issues #431, #878 on GB10) → all gateway pods CrashLoop. Leave the lab cluster
  (drain + disable kubelet, sudo) before onboarding, or wait for a cgroup-root isolation option.

## Target layout
| Layer | Component | Port | Notes |
|---|---|---|---|
| Agent | NemoClaw / OpenClaw in OpenShell sandbox | 8080 gateway, 18789 UI | policy tier Balanced or Restricted |
| Inference | `trtllm-serve` in TRT-LLM 1.3.0rc13 container | 8355 | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` (22.4 GB) |
| Fallback | current vLLM service (`vllm-edge`) | 8100 | cannot run both large models at once (memory) |

## Commands (to run when approved)
```bash
# 1. free disk (three stale vLLM images, ~69 GB)
docker rmi ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest vllm/vllm-openai:gemma vllm/vllm-openai:cu130-nightly
# 2. pull engine + model
docker pull nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13
uv run hf download nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
# 3. serve (trial), YAML keeps memory for the sandbox and RAG
cat > nano.yaml <<'Y'
kv_cache_config:
  enable_block_reuse: false
  free_gpu_memory_fraction: 0.5
  mamba_ssm_cache_dtype: float32
max_batch_size: 8
Y
docker run --rm --gpus all --ipc host --network host -e PYTORCH_ALLOC_CONF=expandable_segments:True \
  -v ~/.cache/huggingface:/root/.cache/huggingface -v $PWD/nano.yaml:/nano.yaml \
  nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13 \
  trtllm-serve serve nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 --host 127.0.0.1 --port 8355 \
  --trust_remote_code --reasoning_parser nano-v3 --tool_parser qwen3_coder --extra_llm_api_options /nano.yaml
# 4. verify with the same scripts
VLLM_URL=http://127.0.0.1:8355 SERVED_NAME=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 uv run --no-sync scripts/smoke.py
# 5. (sudo) leave the lab k8s cluster, then refresh NemoClaw and onboard
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8355/v1 \
NEMOCLAW_MODEL=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 \
NEMOCLAW_COMPATIBLE_AUTH_MODE=none NEMOCLAW_REASONING=true nemoclaw onboard --non-interactive
```
Expected: single-stream decode around the model's base speed (no MTP on TRT-LLM/Spark), roughly 40–50 tok/s class for a
3B-active MoE; measure with `vllm bench serve` pointed at :8355 before switching the agent over.
