#!/usr/bin/env bash
# One-shot setup of the edge-agent stack on a fresh DGX Spark (hackathon "day-of" script).
# Stages are idempotent; rerun the whole thing or one stage: `scripts/bootstrap-spark.sh` | `scripts/bootstrap-spark.sh model`
# Needs: DGX OS with docker (user in docker group), network for the pulls (or a bundle, see docs/hackathon-runbook.md).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
IMAGE=nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13
MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
BUNDLE="${BUNDLE:-}"                 # optional dir with trtllm-image.tar and hf-cache.tar (offline preload)
stage() { echo; echo "=== [$1] $(date +%T)"; }

check() { stage check
  [ -f /etc/nv_tegra_release ] && { echo "JETSON detected: $(head -1 /etc/nv_tegra_release)"; echo "  -> see docs/hackathon-runbook.md (Jetson section): Thor keeps the model, Orin needs a smaller one; run stages by hand"; }
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
  docker info --format 'docker {{.ServerVersion}}' ; command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh -s -- -q
  df -h / | awk 'NR==2{print "disk free:", $4}'
  systemctl is-active kubelet 2>/dev/null | grep -q active && echo "WARNING: kubelet active -> image GC and OpenShell k3s conflicts; see runbook" || true
  for p in 8000 8001 8790 8080 8880; do ss -ltn | grep -q ":$p " && echo "WARNING: port $p in use"; done; true; }

python_env() { stage python; uv sync; }

image() { stage image
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then echo "image present"; elif [ -f "$BUNDLE/trtllm-image.tar" ]; then docker load -i "$BUNDLE/trtllm-image.tar"; else docker pull "$IMAGE"; fi; }

model() { stage model
  if ls "$HOME/.cache/huggingface/hub/models--${MODEL//\//--}/snapshots/"*/config.json >/dev/null 2>&1; then echo "model cached"
  elif [ -f "$BUNDLE/hf-cache.tar" ]; then mkdir -p "$HOME/.cache/huggingface" && tar -C "$HOME/.cache/huggingface" -xf "$BUNDLE/hf-cache.tar"
  else uv run hf download "$MODEL"; fi; }

serve() { stage serve
  mkdir -p logs; HOST=0.0.0.0 nohup scripts/serve-trt.sh > logs/trt-serve.log 2>&1 &
  for i in $(seq 1 120); do curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 5; done
  curl -sf http://127.0.0.1:8000/health >/dev/null || { echo "server not healthy"; tail -20 logs/trt-serve.log; exit 1; }
  pgrep -f "scripts/oai_shim[.]py" >/dev/null || nohup uv run --no-sync scripts/oai_shim.py > logs/oai-shim.out 2>&1 &
  pgrep -f "scripts/ops_api[.]py"  >/dev/null || nohup uv run --no-sync scripts/ops_api.py  > logs/ops-api.out  2>&1 &
  sleep 2; VLLM_URL=http://127.0.0.1:8001 uv run --no-sync scripts/smoke.py | tail -1; }

nemoclaw() { stage nemoclaw
  command -v nemoclaw >/dev/null || { NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8001/v1 \
    NEMOCLAW_MODEL=edge-agent NEMOCLAW_PROVIDER_KEY=none NEMOCLAW_REASONING=true NEMOCLAW_SANDBOX_NAME=edge-agent \
    bash <(curl -fsSL https://www.nvidia.com/nemoclaw.sh) || true; hash -r; }
  nemoclaw list 2>/dev/null | grep -q edge-agent || NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8001/v1 NEMOCLAW_MODEL=edge-agent \
    NEMOCLAW_PROVIDER_KEY=none NEMOCLAW_REASONING=true NEMOCLAW_SANDBOX_NAME=edge-agent NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
    nemoclaw onboard --non-interactive --fresh --yes-i-accept-third-party-software || true
  # the three fixes that made the sandbox actually reach the model and the ops API (see docs/plan-trtllm-nemoclaw.md)
  nemoclaw edge-agent policy add local-inference --yes || true
  openshell provider update compatible-endpoint --config OPENAI_BASE_URL=http://host.openshell.internal:8001/v1
  openshell policy update edge-agent --add-endpoint host.openshell.internal:8790:read-write:rest \
    --add-allow host.openshell.internal:8790:GET:/tools --add-allow host.openshell.internal:8790:POST:/call \
    --add-allow host.openshell.internal:8790:POST:/mcp --add-allow host.openshell.internal:8790:GET:/mcp --add-allow host.openshell.internal:8790:DELETE:/mcp \
    --binary /usr/bin/curl --binary /usr/bin/python3 --binary /usr/local/bin/node --rule-name ops-api --wait || true
  nemoclaw edge-agent exec -- mkdir -p /sandbox/bin /sandbox/.openclaw/skills/ops >/dev/null 2>&1 || true
  nemoclaw edge-agent upload scripts/ops-sandbox-helper.sh /sandbox/bin/ops >/dev/null 2>&1 && nemoclaw edge-agent exec -- chmod +x /sandbox/bin/ops >/dev/null 2>&1 || true
  # upload treats the destination as a directory: give it the skill directory, not the file path
  nemoclaw edge-agent upload skills/ops/SKILL.md /sandbox/.openclaw/skills/ops/ >/dev/null 2>&1 || true
  openshell sandbox exec -- sh -c 'openclaw mcp add ops --url http://host.openshell.internal:8790/mcp --transport streamable-http --timeout 600 --connect-timeout 30 --no-probe; openclaw mcp tools ops --include self_heal; openclaw mcp reload' >/dev/null 2>&1 || true
  nemoclaw onboard --resume --non-interactive --yes-i-accept-third-party-software || true
  nemoclaw edge-agent status | grep -E "Inference|Policies"; }

victim() { stage victim; uv run --no-sync scripts/faults.py setup; }

case "${1:-all}" in
  all) check; python_env; image; model; serve; nemoclaw; victim; echo; echo "READY: scripts/nemoclaw-drill.sh bad-config" ;;
  check|image|model|serve|nemoclaw|victim) "$1" ;;
  python) python_env ;;
  *) echo "usage: $0 [all|check|python|image|model|serve|nemoclaw|victim]"; exit 2 ;;
esac
