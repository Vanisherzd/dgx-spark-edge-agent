"""Needle accuracy probe: 8 questions with exact answers over the same synthetic runbook doc bench.py uses.

Run:  uv run --no-sync scripts/probe.py      env: VLLM_URL, SERVED_NAME, THINK=0|1|both (default both)
Prints per-question hit/miss and the score. Catches models that blur details across similar entries.
"""
import os
import time

from openai import OpenAI

BASE = os.environ.get("VLLM_URL", "http://127.0.0.1:8100")
MODEL = os.environ.get("SERVED_NAME", "edge-agent")
MODES = {"0": [False], "1": [True], "both": [False, True]}[os.environ.get("THINK", "both")]
client = OpenAI(base_url=f"{BASE}/v1", api_key="none", timeout=600)

DOC = "\n".join(
    f"[runbook {i}] service web-{i % 7}: if nginx returns 502, check upstream php-fpm-{i % 5} socket, "
    f"restart with systemctl restart php{7 + i % 3}-fpm, verify with curl -I localhost:{8000 + i}."
    for i in range(250)
)
# (question, expected substring). Each asks about one specific runbook so the answer is unambiguous.
QS = [
    ("In runbook 3, which php-fpm service is restarted?", "php7-fpm"),
    ("In runbook 17, which php-fpm service is restarted?", "php9-fpm"),
    ("In runbook 124, which upstream socket is checked?", "php-fpm-4"),
    ("In runbook 124, which localhost port is used to verify?", "8124"),
    ("In runbook 200, which service (web-N) is documented?", "web-4"),
    ("In runbook 200, which php-fpm service is restarted?", "php9-fpm"),
    ("In runbook 61, which localhost port is used to verify?", "8061"),
    ("In runbook 61, which upstream socket is checked?", "php-fpm-1"),
]
for THINK in MODES:
    hits = 0
    t0 = time.time()
    for q, want in QS:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": "Answer with the exact value from the runbooks only, no explanation."},
                      {"role": "user", "content": DOC + "\n\nQuestion: " + q}],
            max_tokens=1200 if THINK else 40, temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": THINK}},
        )
        got = (r.choices[0].message.content or "").strip().replace("`", "")
        ok = want in got
        hits += ok
        print(f"{'OK  ' if ok else 'MISS'} [think={int(THINK)}] {q} -> {got[:50]!r} (want {want})")
    print(f"SCORE {hits}/{len(QS)} think={int(THINK)} in {time.time() - t0:.0f}s")
