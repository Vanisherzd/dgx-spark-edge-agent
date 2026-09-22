# DGX Spark hardware / host facts (probed 2026-09-21)

| Item | Value |
|---|---|
| Host | `DGX-spark`, `hsnl@192.168.2.60` (lab LAN), also tailscale `100.89.176.56`, wg0 `192.168.10.45` |
| OS | Ubuntu 24.04.4 LTS, kernel `6.17.0-1008-nvidia`, aarch64 |
| CPU | 20 cores (Cortex-X925 + Cortex-A725) |
| Memory | 119 GiB unified (LPDDR5x), shared CPU/GPU |
| GPU | NVIDIA GB10, compute capability 12.1 (`sm_121`), driver 580.126.09, CUDA 13.0 (`/usr/local/cuda-13.0`) |
| Disk | 916 GB NVMe, ~148 GB free at probe time |
| CUDA toolkit | `/usr/local/cuda` = 13.0, `nvcc` present but **not on PATH**; scripts export `CUDA_HOME` and prepend `/usr/local/cuda/bin` (FlashInfer JIT needs it) |
| FlashInfer JIT | keep `MAX_JOBS=4 FLASHINFER_NVCC_THREADS=4`; default ninja parallelism OOM-hangs the box on unified memory (happened 2026-09-22) |
| Python | system 3.12.3 **without headers** (`python3.12-dev` missing → Triton JIT fails). Project uses uv-managed CPython 3.12.14 (`python-preference = only-managed`) |
| Docker | 29.1.3, user `hsnl` in `docker` group; NVIDIA runtime is the default runtime |
| k8s | node in the lab cluster (kubelet, flannel, HAMi webhook). Cordoned 2026-09-21 so no new pods land here. **kubelet image GC** (`imageMinimumGCAge: 0s`, default high threshold 85 % disk) deletes any docker image not used by a running container once `/` is above 85 %: it removed the 35 GB TensorRT-LLM image on 2026-09-22 between two container restarts. Keep `/` below ~80 % or stop kubelet |
| Ollama | systemd service active on :11434, no models loaded |
| sudo | needs password (all project pieces run as user: uv, docker, `systemctl --user`, Linger=yes) |

## Ports in use on the host (before this project)
22 ssh, 7070, 8000 (old `qwen3-server` container, now stopped), 8080 open-webui/traefik, 8081, 8090, 8888, 11434 ollama, 10250/10256 kubelet, 33564.
**This project uses 8100.**

## Stopped on 2026-09-21 (per user: nobody else uses this box)
All non-k8s docker containers were stopped and their restart policy set to `no`
(`qwen3-server` = vLLM Qwen3.8-27B-NVFP4 holding 50 GB GPU, `open-webui`, `ctai_worker`, the `setup-cvat_*` stack, `watchtower`, `setup-certbot-1`, `setup-nginx-1`, `setup-cvat_opa-1`, `cvat_server`).
Bring one back with `docker start <name>` (and `docker update --restart=unless-stopped <name>` if it should survive reboots).
k8s: `kubectl cordon <node>` applied; `comfyui` deployment scaled to 0. Undo: `kubectl uncordon <node>`, `kubectl scale deploy -n comfyui --all --replicas=1`.

## Verified inference stack (2026-09-21)
| Component | Version |
|---|---|
| uv | 0.12.17 (`~/.local/bin/uv`) |
| Python | 3.12.14, uv-managed (`~/.local/share/uv/python/`) |
| vLLM | 0.29.0 (PyPI aarch64 wheel) |
| torch | 2.13.0+cu130 (plain PyPI aarch64 wheel, CUDA 13.0) |
| FlashInfer | 0.6.18 |
| transformers | 5.17.0 |
| Triton | 3.7.1 |
| Model | `Qwen/Qwen3-8B` snapshot `b968826d`, 16.4 GB BF16, in `~/.cache/huggingface` (shared blob store `hub/blobs/`) |

## 2026-09-22 cleanup (disk was at 96 % while pulling the TensorRT-LLM image)
Removed stopped throwaway containers `clever_jackson`, `serene_dijkstra`, `zen_goodall`, `thirsty_grothendieck`,
`competent_varahamihira`, `infallible_tharp`, `flamboyant_tharp`, `vllm-qwen36` and images `vllm/vllm-openai:gemma`,
`vllm/vllm-openai:cu130-nightly` (re-pullable). Kept `ghcr.io/spark-arena/dgx-vllm-eugr-nightly` (used by the stopped
`qwen3-server`). Added: `nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13`, model `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4`.
