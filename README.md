# edge-agent

Offline-capable IT self-healing agent on the lab DGX Spark (GB10, 119 GiB unified memory, aarch64).
Phase 1 = inference infra: vLLM serving `Qwen/Qwen3.6-35B-A3B-FP8` (+ MTP speculative decoding, ~67 tok/s single stream) behind an OpenAI-compatible API. Model/acceleration choice: `docs/bench-2026-09-22.md`. Design: `docs/superpowers/specs/2026-09-21-edge-agent-infra-design.md`. Host facts: `docs/spark-hw.md`.

## Layout
```
pyproject.toml / uv.lock      uv project, Python 3.12, vllm + openai pinned
scripts/download.sh           one-time online model pull into ~/.cache/huggingface
scripts/serve.sh              vllm serve … (offline, 127.0.0.1:8100, model alias "edge-agent", MTP on)
scripts/smoke.py              health → chat → tool call; prints SMOKE OK
scripts/bench.py              decode / concurrency / prefill / thinking throughput
scripts/probe.py              8-question needle accuracy over a synthetic runbook corpus
scripts/try.sh                throwaway server + bench|probe + smoke for a model/flag combo (:8101)
scripts/bench-summary.py      logs/matrix.log → markdown table
scripts/serve-summary.py      vllm bench serve JSON → markdown table (TTFT/TPOT/ITL/E2E)
scripts/serve-trt.sh          TensorRT-LLM alternative: trtllm-serve in the NGC container on :8355 (trt/nano.yaml)
systemd/trtllm-edge.service   user unit for the TensorRT-LLM path (Conflicts= vllm-edge; one engine at a time)
systemd/vllm-edge.service     user unit: auto-start at boot, auto-restart
```

## Setup (once, online)
```bash
cd ~/edge-agent
uv sync                     # recreates .venv from uv.lock (downloads uv-managed CPython 3.12 the first time)
scripts/download.sh         # MODEL=… ; the 35B FP8 is ~35 GB, Qwen3-8B ~16 GB
```

## Run
```bash
scripts/serve.sh            # foreground; start ~6-7 min for the 35B (weights + compile + cudagraphs), ~4 min for Qwen3-8B
uv run scripts/smoke.py     # in another shell
```
Env overrides: `MODEL`, `TOOL_PARSER` (`qwen3_coder` for Qwen3.5+, `hermes` for Qwen3-8B), `SPEC` (speculative-config JSON, empty disables), `SERVED_NAME`, `HOST` (set `0.0.0.0` to expose on the LAN), `PORT`, `MAX_MODEL_LEN`, `MAX_NUM_SEQS`, `GPU_MEM_UTIL`. Extra `vllm serve` flags pass through.
FlashInfer JIT on first start is capped at `MAX_JOBS=4` — never raise it on this box (see docs/bench-2026-09-22.md, incident).

## As a service (survives reboot / crash)
```bash
systemctl --user enable --now ~/edge-agent/systemd/vllm-edge.service
systemctl --user status vllm-edge
journalctl --user -u vllm-edge -f
```

## Client
OpenAI SDK with `base_url="http://127.0.0.1:8100/v1"`, `model="edge-agent"`, any `api_key`.
Qwen3 thinking mode is on by default; pass `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to turn it off per request.

## Serving metrics and quality
`docs/serving-metrics-2026-09-22.md` (TTFT/TPOT/ITL/E2E at 1–16 concurrency, GSM8K 92.7 %, IFEval 78 %, needle 8/8).
TensorRT-LLM + NemoClaw migration: `docs/plan-trtllm-nemoclaw.md`.

## Next (Phase 2)
RAG store + embedding model, remediation tools, bigger model swap (same alias, clients unchanged).
