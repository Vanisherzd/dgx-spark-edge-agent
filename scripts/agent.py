"""Self-healing decision loop: observe -> retrieve runbooks -> decide (tool calls) -> act -> verify.

  uv run --no-sync scripts/agent.py [--case NAME] [--dry-run] [--max-steps 8]
  env: VLLM_URL (default http://127.0.0.1:8355), SERVED_NAME (edge-agent), AGENT_THINK=0 to disable thinking

Scope is deliberately small: two sandbox containers (see scripts/faults.py). Every action goes through a whitelist;
remediation tools become no-ops with --dry-run. Each step is appended to logs/agent/<timestamp>.jsonl.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request

from openai import OpenAI

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONF_DIR = (ROOT / "victim" / "conf.d").resolve()
RUNBOOKS = ROOT / "runbooks"
NAMES = {"edge-victim", "edge-victim-app"}
HEALTH = {"front": "http://127.0.0.1:8880/", "api": "http://127.0.0.1:8880/api/"}
EXEC_ALLOW = ("nginx -t", "nginx -T", "cat ", "ls", "ps", "curl", "df", "du", "tail", "head", "grep", "id", "env")
BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8355")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
THINK = os.environ.get("AGENT_THINK", "1") == "1"

SYSTEM = f"""You are an autonomous IT operations agent keeping a small web stack healthy.
Environment: docker container `edge-victim` (nginx front-end, health URLs {HEALTH['front']} and {HEALTH['api']}) and
`edge-victim-app` (upstream behind /api/). The nginx config lives on the host at victim/conf.d/default.conf.
Rules: gather evidence first (health, container states, logs, config test), consult runbooks, then apply the least
invasive fix, then verify with check_health. Never touch anything outside these two containers and that config
directory. Call `finish` once both health URLs return 200 or when you cannot fix it (status=escalate)."""

TOOLS = [
    {"name": "check_health", "description": "HTTP status of the front-end and /api/ health URLs", "parameters": {"type": "object", "properties": {}}},
    {"name": "docker_ps", "description": "State of the two containers (docker ps -a)", "parameters": {"type": "object", "properties": {}}},
    {"name": "docker_logs", "description": "Last lines of a container's logs", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "tail": {"type": "integer", "default": 40}}, "required": ["name"]}},
    {"name": "docker_exec", "description": "Run a read-only diagnostic command inside a running container (nginx -t, cat, ls, ps, curl, df, du, tail, head, grep)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "cmd": {"type": "string"}}, "required": ["name", "cmd"]}},
    {"name": "docker_start", "description": "Start a stopped container", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "docker_restart", "description": "Restart a container (needed after a config change)", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "read_config", "description": "Read victim/conf.d/default.conf", "parameters": {"type": "object", "properties": {}}},
    {"name": "write_config", "description": "Replace victim/conf.d/default.conf with the given full content, then restart edge-victim is still required", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "search_runbooks", "description": "Keyword search over the runbooks; returns the best matching ones", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "finish", "description": "End the run with a verdict", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["resolved", "unresolved", "escalate"]}, "root_cause": {"type": "string"}, "actions": {"type": "string"}}, "required": ["status", "root_cause", "actions"]}},
]


def sh(cmd, timeout=30):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    return out[:4000] if out else f"(exit {r.returncode}, no output)"


def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


def search_runbooks(query):
    terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for f in RUNBOOKS.glob("*.md"):
        text = f.read_text()
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        scored.append((len(terms & words), f.name, text))
    scored.sort(reverse=True)
    return "\n\n".join(f"## {n}\n{t.strip()[:1500]}" for s, n, t in scored[:2] if s)


def run_tool(name, args, dry_run):
    def guard(n):
        if n not in NAMES:
            return f"denied: container {n!r} is outside the allowed set {sorted(NAMES)}"
    if name == "check_health":
        return json.dumps({k: http_status(u) for k, u in HEALTH.items()})
    if name == "docker_ps":
        return sh("docker ps -a --filter name=edge-victim --format '{{.Names}}\t{{.Status}}'")
    if name == "docker_logs":
        return guard(args["name"]) or sh(f"docker logs --tail {int(args.get('tail', 40))} {args['name']}")
    if name == "docker_exec":
        cmd = args["cmd"].strip()
        if not cmd.startswith(EXEC_ALLOW) or any(t in cmd for t in (";", "&&", "|", ">", "`", "$(")):
            return "denied: only read-only diagnostics are allowed here"
        return guard(args["name"]) or sh(f"docker exec {args['name']} sh -c {json.dumps(cmd)}")
    if name in ("docker_start", "docker_restart"):
        d = guard(args["name"])
        if d:
            return d
        if dry_run:
            return f"DRY-RUN: would run docker {name.split('_')[1]} {args['name']}"
        out = sh(f"docker {name.split('_')[1]} -t 5 {args['name']}" if name == "docker_restart" else f"docker start {args['name']}")
        time.sleep(2)
        return out + "\n" + sh("docker ps -a --filter name=edge-victim --format '{{.Names}}\t{{.Status}}'")
    if name == "read_config":
        return (CONF_DIR / "default.conf").read_text()
    if name == "write_config":
        content = args["content"]
        if len(content) > 20000 or "proxy_pass" not in content:
            return "denied: config must stay under 20 KB and keep the /api/ proxy_pass"
        if dry_run:
            return "DRY-RUN: would write default.conf"
        (CONF_DIR / "default.conf").write_text(content)
        return "written; restart edge-victim to apply"
    if name == "search_runbooks":
        return search_runbooks(args["query"]) or "no runbook matched"
    return f"unknown tool {name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-steps", type=int, default=8)
    a = ap.parse_args()
    client = OpenAI(base_url=f"{BASE}/v1", api_key="none", timeout=300)
    log_dir = ROOT / "logs" / "agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.case or 'adhoc'}.jsonl").open("w")
    trace = lambda **kw: (log.write(json.dumps(kw, ensure_ascii=False) + "\n"), log.flush())

    observation = json.dumps({"health": {k: http_status(u) for k, u in HEALTH.items()},
                              "containers": sh("docker ps -a --filter name=edge-victim --format '{{.Names}}\t{{.Status}}'")})
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Alert: health check degraded. Initial observation: {observation}\nDiagnose and repair."}]
    trace(step=0, observation=observation)
    tools = [{"type": "function", "function": t} for t in TOOLS]
    verdict = None
    for step in range(1, a.max_steps + 1):
        t0 = time.time()
        r = client.chat.completions.create(model=MODEL, messages=messages, tools=tools, tool_choice="auto",
                                           temperature=0, max_tokens=1500,
                                           extra_body={"chat_template_kwargs": {"enable_thinking": THINK}})
        m = r.choices[0].message
        reasoning = getattr(m, "reasoning_content", None) or (m.model_extra or {}).get("reasoning_content")
        calls = m.tool_calls or []
        messages.append({"role": "assistant", "content": m.content or "", "tool_calls": [c.model_dump() for c in calls]} if calls
                        else {"role": "assistant", "content": m.content or ""})
        if not calls:
            trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000], text=m.content)
            messages.append({"role": "user", "content": "Use a tool, or call finish."})
            continue
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if c.function.name == "finish":
                verdict = args
                trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000], finish=args)
                break
            result = run_tool(c.function.name, args, a.dry_run)
            trace(step=step, latency=round(time.time() - t0, 1), reasoning=(reasoning or "")[:2000],
                  tool=c.function.name, args=args, result=result[:2000])
            print(f"[{step}] {c.function.name}({json.dumps(args)[:120]}) -> {result[:160]!r}")
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})
        if verdict:
            break
    final = {k: http_status(u) for k, u in HEALTH.items()}
    trace(step="final", health=final, verdict=verdict)
    print("verdict:", json.dumps(verdict, ensure_ascii=False))
    print("final health:", final)
    return 0 if all(v == 200 for v in final.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
