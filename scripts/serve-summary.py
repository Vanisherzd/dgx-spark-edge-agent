"""Summarize `vllm bench serve --save-result` JSON files into a markdown table.

Run:  uv run --no-sync scripts/serve-summary.py logs/bench-serve/*.json
Columns: concurrency, req/s, output tok/s, TTFT p50/p99 (ms), TPOT mean/p99 (ms), ITL p99 (ms), E2E p50/p99 (s), MTP acceptance.
"""
import json
import sys


def g(d, *keys, nd=1):
    for k in keys:
        if k in d and d[k] is not None:
            v = d[k]
            return f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v)
    return "-"


rows = []
for path in sys.argv[1:]:
    d = json.load(open(path))
    name = path.rsplit("/", 1)[-1].removesuffix(".json")
    if name == "dryrun":
        continue
    rows.append((name, d))
rows.sort(key=lambda r: r[0])
print("| run | conc | req/s | out tok/s | TTFT p50 / p99 ms | TPOT mean / p99 ms | ITL p99 ms | E2E p50 / p99 s | MTP acc len (rate) |")
print("|---|---|---|---|---|---|---|---|---|")
for name, d in rows:
    e50 = d.get("p50_e2el_ms") or d.get("median_e2el_ms")
    e99 = d.get("p99_e2el_ms")
    e = f"{e50/1000:.2f} / {e99/1000:.2f}" if e50 and e99 else "-"
    print(f"| {name} | {g(d,'max_concurrency',nd=0)} | {g(d,'request_throughput',nd=2)} | {g(d,'output_throughput',nd=0)} | "
          f"{g(d,'p50_ttft_ms','median_ttft_ms',nd=0)} / {g(d,'p99_ttft_ms',nd=0)} | {g(d,'mean_tpot_ms',nd=1)} / {g(d,'p99_tpot_ms',nd=1)} | "
          f"{g(d,'p99_itl_ms',nd=1)} | {e} | {g(d,'spec_decode_acceptance_length',nd=2)} ({g(d,'spec_decode_acceptance_rate',nd=0)}%) |")
