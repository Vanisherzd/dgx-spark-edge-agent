# edge-agent

Offline-capable IT self-healing agent on the lab DGX Spark (GB10, 119 GiB unified memory, aarch64).
Phase 1 = inference infra: vLLM serving `Qwen/Qwen3-8B` behind an OpenAI-compatible API. Design: `docs/superpowers/specs/2026-09-21-edge-agent-infra-design.md`. Host facts: `docs/spark-hw.md`.

## Layout
```
pyproject.toml / uv.lock      uv project, Python 3.12, vllm + openai pinned
scripts/download.sh           one-time online model pull into ~/.cache/huggingface
scripts/serve.sh              vllm serve … (offline, 127.0.0.1:8100, model alias "edge-agent")
scripts/smoke.py              health → chat → tool call; prints SMOKE OK
systemd/vllm-edge.service     user unit: auto-start at boot, auto-restart
```

## Setup (once, online)
```bash
cd ~/edge-agent
uv sync                     # recreates .venv from uv.lock (downloads uv-managed CPython 3.12 the first time)
scripts/download.sh         # ~16 GB
```

## Run
```bash
scripts/serve.sh            # foreground; first start ~1-2 min (weights + torch.compile + cudagraphs)
uv run scripts/smoke.py     # in another shell
```
Env overrides: `MODEL`, `SERVED_NAME`, `HOST` (set `0.0.0.0` to expose on the LAN), `PORT`, `MAX_MODEL_LEN`, `GPU_MEM_UTIL`. Extra `vllm serve` flags pass through.

## As a service (survives reboot / crash)
```bash
systemctl --user enable --now ~/edge-agent/systemd/vllm-edge.service
systemctl --user status vllm-edge
journalctl --user -u vllm-edge -f
```

## Client
OpenAI SDK with `base_url="http://127.0.0.1:8100/v1"`, `model="edge-agent"`, any `api_key`.
Qwen3 thinking mode is on by default; pass `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to turn it off per request.

## Next (Phase 2)
RAG store + embedding model, remediation tools, bigger model swap (same alias, clients unchanged).
