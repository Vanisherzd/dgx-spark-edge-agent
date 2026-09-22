# Serving metrics: production server (Qwen3.6-35B-A3B-FP8 + MTP3, vLLM 0.29, DGX Spark)

Tool: `vllm bench serve --backend openai --endpoint /v1/completions --ignore-eos --percentile-metrics ttft,tpot,itl,e2el`
against the live service on `127.0.0.1:8100` (`--max-num-seqs 8`, `--max-model-len 32768`, `--gpu-memory-utilization 0.45`).
Raw JSON: `logs/bench-serve/*.json`; table: `uv run --no-sync scripts/serve-summary.py logs/bench-serve/*.json`.
Datasets: `random` (synthetic token ids, worst case for speculative decoding) and `custom` JSONL of IT-ops prompts
(`short` ~40 tokens, `rag_med` = 60 runbook lines ≈ 3k tokens + question, `rag_long` = 250 lines ≈ 12.9k tokens + question).

| run | conc | req/s | out tok/s | TTFT p50 / p99 ms | TPOT mean / p99 ms | ITL p99 ms | E2E p50 / p99 s | MTP acc len (rate) |
|---|---|---|---|---|---|---|---|---|
| text_short_out128_c1 | 1 | 0.46 | 59 | 156 / 163 | 16.0 / 17.7 | 53.4 | 2.18 / 2.41 | 3.12 (71%) |
| text_short_out128_c8 | 8 | 1.55 | 199 | 348 / 464 | 37.0 / 45.3 | 189.5 | 5.03 / 6.11 | 3.07 (69%) |
| text_ragmed_out256_c1 | 1 | 0.21 | 54 | 416 / 680 | 16.8 / 18.1 | 56.8 | 4.68 / 5.28 | 3.06 (69%) |
| text_ragmed_out256_c8 | 8 | 0.60 | 153 | 993 / 3451 | 45.6 / 61.3 | 449.5 | 12.93 / 16.63 | 3.12 (71%) |
| text_raglong_out256_c1 | 1 | 0.18 | 46 | 371 / 3330 | 17.5 / 18.3 | 60.7 | 5.02 / 7.70 | 3.24 (75%) |
| text_raglong_out256_c8 | 8 | 0.58 | 149 | 1007 / 2899 | 46.3 / 58.4 | 380.4 | 12.93 / 16.45 | 3.21 (74%) |
| random_in256_out128_c1 | 1 | 0.35 | 45 | 171 / 202 | 21.0 / 29.0 | 52.0 | 2.73 / 3.88 | 2.29 (43%) |
| random_in256_out128_c8 | 8 | 1.05 | 134 | 441 / 552 | 53.8 / 87.6 | 238.0 | 7.01 / 11.64 | 2.33 (44%) |
| random_in2048_out256_c1 | 1 | 0.17 | 44 | 419 / 436 | 21.1 / 25.8 | 52.4 | 5.98 / 6.97 | 2.27 (42%) |
| random_in2048_out256_c4 | 4 | 0.35 | 89 | 701 / 3398 | 37.5 / 61.3 | 275.6 | 10.56 / 16.42 | 2.38 (46%) |
| random_in2048_out256_c8 | 8 | 0.42 | 107 | 851 / 4029 | 64.9 / 120.4 | 303.3 | 16.02 / 31.79 | 2.10 (37%) |
| random_in2048_out256_c16 | 16 | 0.48 | 123 | 16611 / 22944 | 59.7 / 111.4 | 304.6 | 31.21 / 47.83 | 2.38 (46%) |
| random_in8192_out256_c1 | 1 | 0.11 | 29 | 1977 / 2008 | 27.4 / 35.7 | 55.1 | 9.49 / 11.07 | 1.84 (28%) |
| random_in8192_out256_c8 | 8 | 0.31 | 80 | 3405 / 10304 | 78.4 / 125.8 | 407.2 | 24.41 / 36.43 | 2.54 (51%) |

## Reading the numbers
- Single-stream decode on real text: TPOT 16–17.5 ms → ~57–62 tok/s per stream, with MTP accepting 3.1–3.2 of 3 drafts + 1
  (70–75 %). Random-token prompts drop acceptance to ~2.3 and TPOT to 21 ms; treat those rows as the floor.
- TTFT: ~150 ms for short prompts, ~420 ms for a 3k-token RAG prompt, ~2 s for a cold 8k prompt (prefill ≈ 4.1k tok/s).
  The long-RAG p50 of 371 ms is a prefix-cache hit on the shared runbook prefix; p99 3.3 s is the cold first request.
- Concurrency 8 (the server's `--max-num-seqs`): 150–200 tok/s aggregate on real text, TTFT p50 ≈ 1 s with RAG prompts.
  Concurrency 16 exceeds `--max-num-seqs 8`, so half the requests queue and TTFT explodes (16 s p50). Raise `MAX_NUM_SEQS`
  if more parallel agents are expected; KV headroom allows it (570k tokens).
- ITL p99 spikes (200–450 ms at c=8) are prefill interleaving: a new 3k–13k-token prompt arriving pauses the decoders
  briefly. Chunked prefill is on by default; lower `--max-num-batched-tokens` to trade prefill speed for smoother ITL.

## Quality (same server)
- Needle probe (`scripts/probe.py`, 8 exact-answer questions over 250 near-identical runbook lines): 8/8 with thinking
  off (7 s total) and 8/8 with thinking on (36 s).
- lm-eval-harness (`local-chat-completions`, thinking on, temperature 0): see the table below.

| eval | setting | score |
|---|---|---|
| GSM8K (`gsm8k_cot_llama`) | 8-shot multiturn CoT, 150 samples, thinking on, max 3072 tokens, temperature 0 | exact match **92.7 % ± 2.1** (strict = flexible) |
| IFEval | 0-shot, 100 prompts, thinking on, max 4096 tokens | prompt-level strict **78.0 % ± 4.2**, loose 77.0 %; instruction-level strict 76.1 %, loose 75.5 % |
| Needle probe (`scripts/probe.py`) | 8 exact-answer questions over 250 similar runbook lines | 8/8 (thinking off and on) |

Caveats: 150/100 samples give ±2–4 pt confidence intervals; IFEval ran with thinking on and a 4096-token cap, so a
few answers may have been cut off inside the reasoning (counts as a miss). Runtime on this server: GSM8K 19.5 min,
IFEval 24 min at 8 concurrent requests. Results JSON: `logs/lm-eval/edge-agent/`.

## TensorRT-LLM (same scripts), `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4`, trtllm-serve 1.3.0rc13, :8355
| metric | value |
|---|---|
| startup to healthy | 106 s |
| single stream (bench.py, 256 tokens) | 57.9 tok/s |
| 8 concurrent | 237 tok/s aggregate (29.6 per stream) |
| prefill, 12.9k-token prompt | 11,025 tok/s (1.17 s), no prefix cache (block reuse off) |
| long-context decode | 41.7 tok/s |
| thinking on | 58.4 tok/s |
| needle probe | 5/8 (thinking off) / 5/8 (thinking on) |
| GSM8K (`gsm8k_cot_llama`), 150, thinking on | **96.7 % ± 1.5** (strict = flexible), 5 min at 8 concurrent |
| IFEval, 100, thinking on | prompt-level strict **80.0 % ± 4.0** (loose 80.0 %), instruction-level 79.1 %; 13 min at 8 concurrent |
| GPU memory | 34 GiB (`free_gpu_memory_fraction: 0.5`) |
No speculative decoding on this path yet. See `docs/plan-trtllm-nemoclaw.md` for the setup and the issues fixed.

## Engine comparison (same box, same scripts, 2026-09-22)
| | vLLM 0.29 + Qwen3.6-35B-A3B-FP8 + MTP3 | TensorRT-LLM 1.3.0rc13 + Nemotron-3-Nano-30B-A3B-NVFP4 |
|---|---|---|
| startup to healthy | 275 s | 106 s |
| single stream | 60.7 tok/s | 57.9 tok/s (no speculative decoding) |
| 8 concurrent aggregate | 343 tok/s | 237 tok/s |
| prefill 12.9k tokens | 3.5 s cold, 0.3 s prefix hit | 1.17 s, no prefix cache |
| long-context decode | 56.6 tok/s | 41.7 tok/s |
| GSM8K-CoT (150) | 92.7 % | 96.7 % |
| IFEval prompt-strict (100) | 78.0 % | 80.0 % |
| needle probe | 8/8 | 5/8 |
| GPU memory | 52 GiB | 34 GiB |
Reading: Nemotron-3-Nano is the stronger reasoner and instruction follower and starts faster; Qwen3.6-35B-A3B is far
better at pulling exact details out of a long, repetitive context. With a real retriever (few candidate chunks in the
prompt) the needle gap matters less; with corpus-in-prompt RAG it matters a lot.
