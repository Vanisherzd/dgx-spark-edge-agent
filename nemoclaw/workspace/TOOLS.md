# Local notes

- Host ops API: `http://host.openshell.internal:8790` (`/tools`, `/call`, `/mcp`). Reachable from this sandbox by policy rule `ops-api`.
- Shell fallback if the MCP tools are unavailable: `/sandbox/bin/ops tools` and `/sandbox/bin/ops call <tool> '<json>'`.
- Inference: `https://inference.local/v1`, model `edge-agent` (TensorRT-LLM on the host, no internet).
- Health URLs: front `http://127.0.0.1:8880/`, api `http://127.0.0.1:8880/api/` — those are host-side, check them with `check_health`, not with your own shell.
