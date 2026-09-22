#!/usr/bin/env bash
# drill: inject a fault on the victim containers, ask the NemoClaw sandbox agent to fix it through the ops API, verify.
cd ~/edge-agent; export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"; source .venv/bin/activate
CASE="${1:-bad-config}"
echo "=== $(date +%T) reset + inject $CASE"; python scripts/faults.py reset | tail -1; python scripts/faults.py inject "$CASE"
: > logs/ops-api.log
PROMPT='Alert: the web stack is degraded. Your shell has no docker or systemctl. Step 1: run this exact shell command and wait for it: /sandbox/bin/ops call self_heal "{}" — it runs the full diagnose-and-repair loop on the host and prints the steps and verdict. Step 2: run /sandbox/bin/ops call check_health "{}" to confirm both URLs return 200. Only if the stack is still unhealthy, continue manually with /sandbox/bin/ops call <tool> <json> (tools: docker_ps, docker_logs {"name"}, docker_start {"name"}, read_config, write_config {"content"}, docker_restart {"name"}). Do not use tool_search or tool_describe. End with: root cause, actions taken, final health.'
echo "=== $(date +%T) NemoClaw agent turn"
timeout 900 nemoclaw edge-agent agent --agent main --session-id "drill-$(date +%s)" -m "$PROMPT" 2>&1 | grep -vE "UNDICI|trace-warnings|^\s*$|Active gateway|^\[gateway\]" | cut -c1-500
echo "=== $(date +%T) verify"; python scripts/faults.py verify
echo "=== ops-api.log (what the sandbox agent called)"
python3 - <<'PY'
import json
for l in open('logs/ops-api.log'):
    d = json.loads(l)
    print(d['ts'], d['from'], d['tool'], json.dumps(d['args'])[:90], '->', d['result'][:100].replace('\n', ' '))
PY
echo "DRILL_DONE $(date +%T)"
