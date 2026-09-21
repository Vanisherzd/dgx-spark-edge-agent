"""Turn logs/matrix.log (from matrix.sh / try.sh) into a markdown table. Usage: python summarize_matrix.py logs/matrix.log"""
import re, sys
rows, cur = [], None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = re.match(r"\[(\S+)\] healthy after (\d+)s \| GPU KV cache size: ([\d,]+) tokens", line)
    if m:
        cur = {"tag": m.group(1), "start_s": int(m.group(2)), "kv": m.group(3)}; rows.append(cur); continue
    if cur is None: continue
    for key, pat in [("single", r"single stream\s*: .* = ([\d.]+) tok/s"),
                     ("conc8", r"8 concurrent\s*: .* = ([\d.]+) tok/s aggregate"),
                     ("prefill", r"prefill cold\s*: .* = (\d+) tok/s"),
                     ("longctx", r"long-ctx decode\s*: .* = ([\d.]+) tok/s"),
                     ("think", r"thinking on\s*: .* = ([\d.]+) tok/s")]:
        m = re.search(pat, line)
        if m: cur[key] = m.group(1)
    if "SMOKE OK" in line: cur["smoke"] = "OK"
    if line.startswith("FAIL") or "AssertionError" in line: cur["smoke"] = "FAIL"
    if "SERVER DIED" in line: cur["smoke"] = "died"
print("| config | single tok/s | 8-conc agg tok/s | long-ctx decode | thinking | prefill tok/s | KV tokens | start s | smoke |")
print("|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(f"| {r['tag']} | {r.get('single','-')} | {r.get('conc8','-')} | {r.get('longctx','-')} | {r.get('think','-')} | {r.get('prefill','-')} | {r['kv']} | {r['start_s']} | {r.get('smoke','-')} |")
