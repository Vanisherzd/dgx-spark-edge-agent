"""Smoke test for the edge-agent vLLM server: health, plain chat, parsed tool call.

Run:  uv run scripts/smoke.py        (env: VLLM_URL, SERVED_NAME)
"""
import os
import sys
import time
import urllib.request

from openai import OpenAI

BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8100")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}  # keep the smoke test fast and deterministic


def wait_healthy(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=5)
            return time.time() - t0
        except Exception:
            time.sleep(5)
    sys.exit(f"FAIL: {BASE}/health not up after {timeout}s")


waited = wait_healthy()
print(f"health OK after {waited:.0f}s")
client = OpenAI(base_url=f"{BASE}/v1", api_key="none")

r = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Reply with exactly one word: pong"}],
    max_tokens=50,
    extra_body=NO_THINK,
)
content = r.choices[0].message.content or ""
print("chat:", content.strip())
assert "pong" in content.lower(), "chat completion did not answer"

tools = [{
    "type": "function",
    "function": {
        "name": "restart_service",
        "description": "Restart a systemd service on this host",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "systemd unit name"}},
            "required": ["name"],
        },
    },
}]
r = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "nginx is down on this host. Fix it with the tools you have."}],
    tools=tools,
    tool_choice="auto",
    max_tokens=300,
    extra_body=NO_THINK,
)
calls = r.choices[0].message.tool_calls or []
print("tool_calls:", [(c.function.name, c.function.arguments) for c in calls])
assert calls and calls[0].function.name == "restart_service", "no tool call parsed; check --tool-call-parser hermes"
print("SMOKE OK")
