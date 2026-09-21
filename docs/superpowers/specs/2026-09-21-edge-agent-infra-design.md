# Edge Agent – Phase 1 infrastructure design (2026-09-21)

## Goal
An IT self-healing agent that runs entirely on the lab DGX Spark: local LLM inference (vLLM) + RAG over IT runbooks, so it can diagnose and repair problems even with the network down. Phase 1 = inference infrastructure only.

## Scope (Phase 1)
- Project folder `~/edge-agent` on the Spark, git-tracked, uv-managed (Python 3.12, `uv.lock` pinned → reproducible offline rebuild).
- vLLM installed bare-metal from PyPI wheels (aarch64, CUDA 13 / `sm_121`), no containers. Verified stack: uv 0.12.17, uv-managed CPython 3.12.14, vLLM 0.29.0, torch 2.13.0+cu130 (plain PyPI aarch64 wheel), FlashInfer 0.6.18, transformers 5.17.0, Triton 3.7.1.
- Model `Qwen/Qwen3-8B` (BF16, ~16 GB) cached in the shared HF cache; server runs with `HF_HUB_OFFLINE=1`.
- `scripts/serve.sh`: OpenAI-compatible server on `127.0.0.1:8100`, served name `edge-agent`, tool calling (`hermes` parser) + reasoning parser `qwen3`, 32k context, GPU budget 50 % of unified memory.
- `scripts/smoke.py`: health → chat → parsed tool call; exits non-zero on failure.
- `systemd/vllm-edge.service`: user unit, `Restart=always`, starts at boot (Linger is on) — the base for "self-reliant when offline".

## Out of scope (Phase 2+)
RAG store and embedding model, agent loop / tools for remediation, larger model (gpt-oss-120b, Qwen3.x, Llama-3.3-70B), auth/TLS on the API, multi-node.

## Alternatives considered
- Docker `vllm/vllm-openai` / NGC image: more reproducible image, but extra layer (docker + `--privileged` for unified memory) and it is not what the user asked for (uv). Fallback if bare-metal kernels fail on `sm_121`.
- Community `eugr/spark-vllm-b12x` image: only needed for DeepSeek-V4 sparse attention; not applicable.

## Known landmines (from community reports on GB10)
1. FlashInfer JIT needs `ninja` on PATH → always `source .venv/bin/activate` (serve.sh does).
2. NVFP4 models JIT-compile CUTLASS kernels on first start (~7 min, looks hung). Qwen3-8B BF16 avoids this; expect it when moving to an NVFP4 model.
3. System `python3.12` has no headers (`python3.12-dev` not installed, no sudo). Triton compiles a CPython extension (`cuda_utils.c`) during vLLM's torch.compile → `fatal error: Python.h`. Fix: uv-managed CPython (`[tool.uv] python-preference = "only-managed"`), which ships `include/python3.12/Python.h`.
4. `nvcc` is not on PATH on DGX OS (it lives in `/usr/local/cuda/bin`). vLLM's `has_flashinfer()` then reports FlashInfer unavailable (no `flashinfer-cubin` package either) and models that only offer the FLASHINFER/TRITON_ATTN backends (Qwen3.8-27B-NVFP4) die at first decode with `FlashInfer backend is not available`. serve.sh/try.sh export `CUDA_HOME=/usr/local/cuda` and prepend `/usr/local/cuda/bin`.
5. FlashInfer's first JIT build lets ninja fan out ~20 nvcc processes. On unified memory they compete with the loaded weights and the whole box locked up (sshd, kubelet, ollama all unresponsive; needed a power cycle) on 2026-09-22 while compiling the sm_121a CUTLASS FP4 kernels for Qwen3.8-27B-NVFP4. serve.sh/try.sh set `MAX_JOBS=4 FLASHINFER_NVCC_THREADS=4`; compiled ops persist in `~/.cache/flashinfer/0.6.18/121a/`. `flashinfer-jit-cache-sm121a` prebuilt wheels exist on flashinfer.ai/whl/cu130 but only for 0.7.0rc3, not our 0.6.18.
6. Unified memory: `--gpu-memory-utilization` is a fraction of the whole 119 GiB pool and is pre-allocated for KV cache; other GPU users (docker containers, k8s pods, ollama) fight for the same pool. Host cleaned up and node cordoned on 2026-09-21.

## Verification
`uv run scripts/smoke.py` prints `SMOKE OK`; `systemctl --user status vllm-edge` is active; after `systemctl --user restart vllm-edge` with the network cable out, the smoke test still passes.
