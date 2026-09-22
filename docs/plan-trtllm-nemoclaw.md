# Plan: TensorRT-LLM inference + NemoClaw agent layer (drafted 2026-09-22; TRT-LLM part executed the same day)

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

## Execution log (2026-09-22)
1. Pulled `nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13` (35.6 GB) and `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4`.
   Server started (health in 130 s) but every request failed with `PreTrainedTokenizerFast has no attribute tokenizer`
   (`openai_server.py` expects TRT-LLM's `TransformersTokenizer` wrapper; the Omni/multimodal path hands it the raw HF
   tokenizer). Dropped the Omni checkpoint; the agent is text-only anyway.
2. Switched to the text-only `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` (19.3 GB). Requests then failed with
   `'NoneType' object has no attribute 'tokenizer'`: with `HF_HUB_OFFLINE=1` TRT-LLM's tokenizer loader calls the HF
   API (`Failed to load hf tokenizer ... Cannot reach https://huggingface.co/api/models/...`) and silently leaves the
   tokenizer unset. Fix in `scripts/serve-trt.sh`: pass `--tokenizer <cached snapshot dir>`; weights already load
   offline. Also set `--served_model_name edge-agent` so clients see the same id as with vLLM.
3. kubelet image GC deleted the 35 GB TRT-LLM image while the container was restarting (disk was at 88–90 %, above
   the 85 % threshold, `imageMinimumGCAge: 0s`). Freed disk: removed the stopped `qwen3-server` container (its
   `docker inspect` is saved in `logs/qwen3-server.inspect.json`) and image, the stopped throwaway vLLM containers and
   images, the Omni checkpoint and the DFlash drafters. Disk at 85 % afterwards; the durable fix is to stop kubelet.
4. Default `max_num_tokens=8192` with chunked prefill off rejected the 12.9k-token RAG prompt; `trt/nano.yaml` now sets
   `max_seq_len: 32768`, `enable_chunked_prefill: true`.
5. First working numbers (before item 4, text-only NVFP4, no speculative decoding): health in 106 s, single stream
   57.9 tok/s, 8 concurrent 233 tok/s aggregate, smoke (tool call) OK. GPU 33.5 GiB.
6. With the YAML fix (text-only NVFP4, `--served_model_name edge-agent`, no speculative decoding): health 106 s,
   single stream 57.9 tok/s, 8 concurrent 237 tok/s, prefill 11,025 tok/s (12.9k-token prompt in 1.17 s; block reuse
   is off so there is no prefix-cache benefit), long-context decode 41.7 tok/s, thinking 58.4 tok/s, GPU 34 GiB.
   Smoke (tool call) OK. **Needle probe 5/8** in both thinking modes (Qwen3.6-35B-A3B and Qwen3.8-27B scored 8/8):
   Nemotron-3-Nano confuses details across near-identical runbook lines. Mitigations: real retrieval (few candidates
   in context) instead of dumping the whole corpus, or a stronger TRT-LLM-validated model (Nemotron-3-Super-120B-A12B
   NVFP4, gpt-oss-120b MXFP4) at lower speed.

## Tuning matrix (2026-09-22, text-only Nemotron NVFP4)
| variant | result |
|---|---|
| baseline + `TRTLLM_ENABLE_PDL=1` | 57.8 tok/s single, 236 tok/s at 8, prefill 11.1k tok/s: no measurable change vs. PDL off |
| `kv_cache_config.dtype: fp8` | not measured: the run hit a port race with the previous container (fixed with a wait-for-port loop) |
| NGram speculative decoding | server healthy but every request failed with `CUDA error: device-side assert triggered` during CUDA graph capture; unusable on this model/version |
| `enable_block_reuse: true` (prefix cache) | works but hurts: first 12.9k-token prompt took 18.65 s (vs 1.16 s), a repeated prefix was not faster (1.18 s). Kept off. |
Also: the needle probe moved between 5/8 and 7/8 across identical configs at temperature 0, so treat single-run probe
scores as ±1–2.

Final production config: `trt/nano.yaml` (baseline), started manually with `scripts/serve-trt.sh` (no autostart by request). Port moved from 8355 to **8000**: NemoClaw only auto-rewrites loopback endpoints on its bundled host-gateway ports (8000, 11434, 11435); on 8355 the sandbox got 503/403.

## Host changes on 2026-09-22 (user-approved)
Left the lab Kubernetes cluster: `kubectl drain dgx-spark`, `systemctl disable --now kubelet cri-docker`, removed the
19 `k8s_*` pod containers. The node object still exists in the cluster (`kubectl delete node dgx-spark` when the admin
wants it gone). `vllm-edge` autostart removed; nothing starts at boot now (per user request). All lab containers
(CVAT, ctai, open-webui, watchtower, autoresearch) are kept, stopped.

## NemoClaw install log (2026-09-22)
- The March checkout (`~/workspace/NemoClaw`, npm-linked as `/usr/bin/nemoclaw`, OpenShell 0.0.11) was 5,577 commits
  behind. The official installer refused twice: first because it could not read the old CLI's version, then because
  the old sandbox state needed the "experimental OpenShell upgrade". Fix: removed the two stale symlinks
  (`/usr/bin/nemoclaw`, `/usr/lib/node_modules/nemoclaw`; the checkout itself is untouched) and moved `~/.nemoclaw` to
  `~/.nemoclaw.bak-2026-09-22`. Third run installed NemoClaw v0.0.124 + OpenShell 0.0.116 into `~/.npm-global` and
  `~/.local/bin`, and started the gateway `nemoclaw` at https://127.0.0.1:8080 (mTLS).
- Pitfall: on a DGX Spark the installer runs `nemoclaw onboard --non-interactive` by itself (express path) and starts
  downloading the default `nvidia/Qwen3.6-35B-A3B-NVFP4` for a managed vLLM on :8000, which would fight the
  TensorRT-LLM server for memory. Killed it; the partial download is still in the HF cache (~35 GB) until someone
  removes it (`hf cache rm model/nvidia/Qwen3.6-35B-A3B-NVFP4`).
- Onboarding is redone with `NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8355/v1
  NEMOCLAW_MODEL=edge-agent NEMOCLAW_COMPATIBLE_AUTH_MODE=none NEMOCLAW_REASONING=true nemoclaw onboard
  --non-interactive --fresh`. The gateway is a host process, so a loopback endpoint is reachable from it.
- Sudo for the installer was fed through a temporary `SUDO_ASKPASS` helper that was deleted afterwards.
