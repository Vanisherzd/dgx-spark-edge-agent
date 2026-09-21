"""Quick serving benchmark against the edge-agent server. Prints one summary block.

Run:  uv run --no-sync scripts/bench.py          env: VLLM_URL, SERVED_NAME, CONC (default 8), TOKENS (default 256)
Measures: single-stream decode tok/s, CONC-way aggregate tok/s, long-prompt prefill tok/s (cold + prefix-cached),
thinking-mode decode tok/s. Bandwidth-bound hardware (DGX Spark) => single-stream ~ bandwidth / bytes-read-per-token.
"""
import concurrent.futures as cf
import os
import time

from openai import OpenAI

BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8100")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
CONC = int(os.environ.get("CONC", "8"))
TOKENS = int(os.environ.get("TOKENS", "256"))
client = OpenAI(base_url=f"{BASE}/v1", api_key="none", timeout=600)

Q = "Explain in detail, step by step, how to debug a Linux server whose nginx returns 502 Bad Gateway."
DOC = "\n".join(
    f"[runbook {i}] service web-{i % 7}: if nginx returns 502, check upstream php-fpm-{i % 5} socket, "
    f"restart with systemctl restart php{7 + i % 3}-fpm, verify with curl -I localhost:{8000 + i}."
    for i in range(250)
)  # ~12k tokens, stays under a 32k context


def run(prompt=Q, n=TOKENS, think=False):
    t = time.time()
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=n, temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": think}},
    )
    return r.usage.prompt_tokens, r.usage.completion_tokens, time.time() - t, r.choices[0].message.content or ""


run(n=16)  # warm-up
p, tok, dt, _ = run()
print(f"single stream      : {tok} tok / {dt:.1f}s = {tok / dt:.1f} tok/s")
with cf.ThreadPoolExecutor(CONC) as ex:
    res = list(ex.map(lambda _: run(), range(CONC)))
tot = sum(r[1] for r in res)
mx = max(r[2] for r in res)
print(f"{CONC:>2} concurrent      : {tot} tok / {mx:.1f}s = {tot / mx:.1f} tok/s aggregate ({tot / CONC / mx:.1f} per stream)")
p, tok, dt, _ = run(DOC + "\n\nQuestion: which php-fpm socket does web-3 use?", n=1)
print(f"prefill cold       : {p} prompt tok / {dt:.2f}s = {p / dt:.0f} tok/s")
p, tok, dt, _ = run(DOC + "\n\nQuestion: how do I verify web-3 is back?", n=1)
print(f"prefill prefix-hit : {p} prompt tok / {dt:.2f}s")
p, tok, dt, _ = run(DOC + "\n\nQuestion: web-3 returns 502. Give the exact fix commands.", n=TOKENS)
print(f"long-ctx decode    : {tok} tok / {dt:.1f}s = {tok / dt:.1f} tok/s (after {p} prompt tok)")
p, tok, dt, out = run("A Linux host lost its default route after a DHCP renew. Diagnose and fix. Be concise.", n=400, think=True)
print(f"thinking on        : {tok} tok / {dt:.1f}s = {tok / dt:.1f} tok/s")
print("--- sample (RAG question, no-think) ---")
print(run(DOC + "\n\nQuestion: web-3 returns 502. Give the exact fix commands.", n=160)[3].strip()[:600])
