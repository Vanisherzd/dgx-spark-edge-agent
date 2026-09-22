# Hackathon runbook: fresh DGX Spark to working edge-agent in ~30 minutes

Everything below was done once on the lab Spark (2026-09-21/22) and is captured in this repo; the day-of job is to
replay it. Official stack: TensorRT-LLM (trtllm-serve container) + Nemotron-3-Nano-30B-A3B-NVFP4 + NemoClaw.

## Before the event (do at the lab, once)
1. Bring this repo on the laptop (`~/Desktop/HSNL/edge-agent`) and, if the venue network is uncertain, an offline bundle:
   ```bash
   # on the lab Spark
   docker save nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13 -o /tmp/bundle/trtllm-image.tar          # 35.6 GB
   tar -C ~/.cache/huggingface -cf /tmp/bundle/hf-cache.tar hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 hub/blobs   # ~19 GB
   ```
   Put both on an SSD; `BUNDLE=/media/ssd/bundle scripts/bootstrap-spark.sh` uses them instead of pulling.
   NemoClaw's installer and `uv sync` still need network (small downloads).
2. Rehearse `scripts/bootstrap-spark.sh` on the lab Spark from a clean shell.

## Day of, on the new machine
```bash
git clone <this repo> ~/edge-agent && cd ~/edge-agent      # or copy from the laptop
scripts/bootstrap-spark.sh                                 # stages: check python image model serve nemoclaw victim
scripts/nemoclaw-drill.sh bad-config                       # NemoClaw agent fixes an injected fault
uv run --no-sync scripts/faults.py run upstream-down       # same drill with the in-repo agent loop (no NemoClaw)
```
Timing on the lab Spark: image pull 5 min, model 3 min, server to healthy 2 min, NemoClaw install+onboard 8 min.

## Things that bit us (all fixed in the scripts, listed so nobody re-debugs them)
- Server must bind 0.0.0.0 on port 8000: NemoClaw's sandbox reaches it via `host.openshell.internal:8000`; other ports get
  rewritten to NemoClaw's Ollama proxy (401). `scripts/serve-trt.sh` refuses to restart a healthy server (FORCE=1).
- `scripts/oai_shim.py` must sit in front of TensorRT-LLM: it rejects OpenClaw's follow-up messages (400) otherwise.
- Sandbox starts with no policy: add `local-inference`, then the ops-api endpoint rule (in bootstrap).
- Text-only Nemotron checkpoint only; the Omni one trips a tokenizer bug in trtllm-serve 1.3.0rc13.
- `--tokenizer` must be the cached snapshot path when `HF_HUB_OFFLINE=1`.
- Never let kubelet run on the box (image GC deletes images at >85 % disk; OpenShell k3s fights it). Never raise FlashInfer
  `MAX_JOBS` on the vLLM path.
- The installer auto-runs "express" onboarding on a Spark and starts downloading a 35 B model: the bootstrap pre-sets the
  provider env so it uses ours; if it still does, `pkill -f "nemoclaw onboard"` and stop the `nemoclaw-hf-download-*` container.

## What to demo
`scripts/nemoclaw-drill.sh <case>` prints the agent's report, the health verification and the ops-API call log
(`logs/ops-api.log`: every tool the sandbox agent invoked, with args and results). Cases: nginx-stopped, bad-config,
upstream-down. New cases: add an `inject` branch in `scripts/faults.py` and a runbook in `runbooks/`.
