# dgx-spark-edge-agent — 在 DGX Spark 上離線自我修復的 IT Agent

**目標**：一台 NVIDIA DGX Spark（GB10, 128 GB unified memory）上，完全本地跑大模型推論 + 小型 RAG，做一個「網路斷了也能自己診斷、自己修好」的 IT 維運 Agent。合規要求使用 NVIDIA 官方堆疊：**TensorRT-LLM**（推論）+ **NemoClaw**（Agent 執行環境）。

**現況（2026-09-22）**：三個錯誤注入案例（服務掛掉 / 設定檔壞掉 / 上游 502）在兩條路徑都自主修復成功——repo 內的 agent 迴圈 3/3、透過 NemoClaw sandbox agent 3/3。

```
            ┌────────────── DGX Spark（全部本地，不需外網）──────────────┐
  你 / 聊天  │  NemoClaw CLI ──► OpenShell sandbox（OpenClaw agent）          │
  頻道       │        │ inference.local        │ /sandbox/bin/ops / MCP       │
            │        ▼                        ▼                              │
            │  oai_shim.py :8001 ──► trtllm-serve :8000     ops_api.py :8790  │
            │  （格式相容層）        Nemotron-3-Nano-30B-A3B   （白名單工具、HTTP+MCP）│
            │                       NVFP4, TensorRT-LLM          │            │
            │                                                    ▼            │
            │                     agent.py 自我修復迴圈 ──► docker（victim 容器）│
            └───────────────────────────────────────────────────────────────┘
  備援：vLLM 0.29 + Qwen3.6-35B-A3B-FP8 + MTP（scripts/serve.sh, :8100）
```

## 目錄

1. [硬體與已驗證版本](#1-硬體與已驗證版本)
2. [Repo 結構](#2-repo-結構)
3. [從零搭建（逐步）](#3-從零搭建逐步)
4. [日常操作](#4-日常操作)
5. [我們做過的測試與數據](#5-我們做過的測試與數據)
6. [錯誤注入與自我修復 drill](#6-錯誤注入與自我修復-drill)
7. [新增你自己的錯誤注入案例](#7-新增你自己的錯誤注入案例)
8. [踩過的坑（一定要知道）](#8-踩過的坑一定要知道)
9. [Hackathon 當天流程](#9-hackathon-當天流程)
10. [安全與待辦](#10-安全與待辦)
11. [文件索引](#11-文件索引)

---

## 1. 硬體與已驑證版本

| 項目 | 值 |
|---|---|
| 機器 | DGX Spark，GB10 Grace Blackwell（sm_121），20 核 ARM，119 GiB unified memory（CPU/GPU 共用，**沒有 swap**），NVMe 916 GB |
| OS / 驅動 | Ubuntu 24.04.4（DGX OS）, kernel 6.17 nvidia, driver 580.126.09, **CUDA 13.0**（`/usr/local/cuda`，nvcc 不在 PATH） |
| 推論（正式） | TensorRT-LLM 容器 `nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13`（arm64），模型 `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4`（19.3 GB） |
| 推論（備援） | vLLM 0.29.0 + torch 2.13.0+cu130（純 PyPI aarch64 wheel），FlashInfer 0.6.18，模型 `Qwen/Qwen3.6-35B-A3B-FP8` + MTP |
| Python / 套件 | uv 0.12.17，uv 管理的 CPython 3.12.14（系統 python3.12 沒 headers），`uv.lock` 全部 252 套件鎖版 |
| Agent 執行環境 | NemoClaw v0.0.124，OpenShell 0.0.116（docker sandbox），OpenClaw 2026.7.1 |
| 帶寬上限 | LPDDR5X ≈ 273 GB/s → 單流 decode ≈ 273 ÷（每 token 讀的權重 GB）tok/s；這是模型選擇的核心限制 |

## 2. Repo 結構

```
pyproject.toml / uv.lock        uv 專案（Python 3.12，vllm、openai、lm-eval 等鎖版）
scripts/
  serve-trt.sh                  正式：trtllm-serve 容器，:8000（HOST=0.0.0.0 給 NemoClaw 用）。已在跑就拒絕重啟（FORCE=1 強制）
  serve.sh                      備援：vLLM + Qwen3.6-35B-A3B-FP8 + MTP，:8100
  oai_shim.py                   :8001 → :8000 的 OpenAI 格式相容層（NemoClaw/OpenClaw 的請求 TRT-LLM 會拒絕，見第 8 節）
  ops_api.py                    host 端工具 API :8790（HTTP `/tools` `/call` + MCP `/mcp`），只暴露白名單工具 + `self_heal`
  agent.py                      自我修復迴圈：觀察 → 工具呼叫決策 → 動作 → 驗證，JSONL 軌跡在 logs/agent/
  faults.py                     victim 沙箱（兩個 nginx 容器）與錯誤注入：nginx-stopped / bad-config / upstream-down
  nemoclaw-drill.sh             注入錯誤 → 叫 NemoClaw sandbox agent 修 → 驗證 → 印出它呼叫過的工具
  ops-sandbox-helper.sh         上傳到 sandbox 的 /sandbox/bin/ops（`ops tools` / `ops call <tool> '<json>'`）
  smoke.py / probe.py           健康+tool call 煙霧測試；8 題 needle 精準度探針
  bench.py / serve-summary.py / bench-summary.py   吞吐測試與結果表
  try.sh                        對任一模型/參數組合：起臨時 server(:8101) → bench → smoke → 關掉
  download.sh                   一次性下載模型到 ~/.cache/huggingface（之後全離線）
  bootstrap-spark.sh            新機器一鍵重建（含 Jetson 偵測）
skills/ops/SKILL.md             OpenClaw skill，教 sandbox agent 用 ops 指令
runbooks/*.md                   小型 RAG 語料（IT runbook）
victim/conf.d/                  victim nginx 設定（由 faults.py 產生，不入版控）
trt/*.yaml                      trtllm-serve 的 extra_llm_api_options（nano.yaml 為正式）
systemd/*.service               user unit（目前刻意不啟用開機自啟）
docs/                           詳細文件（見第 11 節）
```

## 3. 從零搭建（逐步）

一鍵版：`scripts/bootstrap-spark.sh`（stages：check → python → image → model → serve → nemoclaw → victim，可單獨跑 `scripts/bootstrap-spark.sh model`）。以下是它做的事、為什麼、以及每步驟的驗證方式。

### 3.0 前置：把機器清乾淨（需要 sudo 一次）

```bash
nvidia-smi; docker info --format '{{.ServerVersion}}'; df -h /          # 需 docker ≥28、磁碟 ≥ 60 GB 可用
# 這台原本是 lab k8s worker：kubelet 的 image GC 會在磁碟 >85% 時刪掉沒在用的 image（我們被刪過一次 35 GB），
# OpenShell 的 sandbox 也會跟宿主 kubelet 打架 → 退出 cluster
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force
sudo systemctl disable --now kubelet cri-docker cri-docker.socket
sudo systemctl disable --now ollama                                       # 純 TensorRT-LLM，不用 Ollama
```
其他 lab 服務容器只停不刪（`docker stop`，restart policy 改 `no`）。

### 3.1 uv + Python 環境

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Vanisherzd/dgx-spark-edge-agent.git ~/edge-agent && cd ~/edge-agent
uv sync        # 第一次會下載 uv 管理的 CPython 3.12（pyproject 鎖 python-preference=only-managed，因為系統 python 沒 Python.h，Triton JIT 會炸）
```

### 3.2 推論引擎與模型（正式：TensorRT-LLM）

```bash
docker pull nvcr.io/nvidia/tensorrt-llm/release:1.3.0rc13                  # 35.6 GB，約 5 分
uv run hf download nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4             # 19.3 GB，約 3 分（純文字版；Omni 版在 1.3.0rc13 有 tokenizer bug）
HOST=0.0.0.0 nohup scripts/serve-trt.sh > logs/trt-serve.log 2>&1 &        # 約 2 分到 /health
curl -sf http://127.0.0.1:8000/health && curl -s http://127.0.0.1:8000/v1/models   # 看到 "edge-agent"
VLLM_URL=http://127.0.0.1:8000 uv run --no-sync scripts/smoke.py           # 期待 SMOKE OK（chat 回 pong + tool call 解析成功）
```
`serve-trt.sh` 做了三件非顯而易見的事：`--tokenizer` 指到本機 snapshot 路徑（離線模式下 TRT-LLM 載 tokenizer 會打 HF API）、`--served_model_name edge-agent`（client 不隨模型換而改）、YAML 開 chunked prefill 並設 `max_seq_len 32768`（預設 `max_num_tokens 8192` 會拒絕長 RAG prompt）。

### 3.3 相容層與工具 API

```bash
nohup uv run --no-sync scripts/oai_shim.py > logs/oai-shim.out 2>&1 &     # :8001，NemoClaw 打這裡
nohup uv run --no-sync scripts/ops_api.py  > logs/ops-api.out  2>&1 &     # :8790，sandbox agent 的手腳
curl -s http://127.0.0.1:8790/tools | head -c 200
```

### 3.4 NemoClaw（Agent 執行環境）

```bash
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL=http://127.0.0.1:8001/v1 NEMOCLAW_MODEL=edge-agent NEMOCLAW_PROVIDER_KEY=none \
NEMOCLAW_REASONING=true NEMOCLAW_SANDBOX_NAME=edge-agent \
bash <(curl -fsSL https://www.nvidia.com/nemoclaw.sh)          # 裝 Node/OpenShell/CLI；在 Spark 上會自動 onboard
# 若它跑去 express 路徑下載 35B 模型：pkill -f "nemoclaw onboard"; docker stop $(docker ps -q --filter name=nemoclaw-hf-download)
nemoclaw onboard --non-interactive --fresh --yes-i-accept-third-party-software   # 帶同一組 NEMOCLAW_* 環境變數

# 三個讓 sandbox 真的打到模型的修正（不做這三步 onboarding 第 7 步永遠 503/403/401）
nemoclaw edge-agent policy add local-inference --yes
openshell provider update compatible-endpoint --config OPENAI_BASE_URL=http://host.openshell.internal:8001/v1
openshell policy update edge-agent --add-endpoint host.openshell.internal:8790:read-write:rest \
  --add-allow host.openshell.internal:8790:GET:/tools --add-allow host.openshell.internal:8790:POST:/call \
  --add-allow host.openshell.internal:8790:POST:/mcp --add-allow host.openshell.internal:8790:GET:/mcp \
  --binary /usr/bin/curl --binary /usr/bin/python3 --binary /usr/local/bin/node --rule-name ops-api --wait
nemoclaw onboard --resume --non-interactive --yes-i-accept-third-party-software   # 補完第 7、8 步
nemoclaw edge-agent status                                                     # Inference: healthy

# 給 sandbox agent 工具：shell helper + skill + MCP（只曝露 self_heal）
nemoclaw edge-agent exec -- mkdir -p /sandbox/bin /sandbox/.openclaw/skills/ops
nemoclaw edge-agent upload scripts/ops-sandbox-helper.sh /sandbox/bin/ops && nemoclaw edge-agent exec -- chmod +x /sandbox/bin/ops
nemoclaw edge-agent upload skills/ops/SKILL.md /sandbox/.openclaw/skills/ops/     # 目的地給「目錄」，upload 會把檔名路徑當目錄
openshell sandbox exec -- sh -c 'openclaw mcp add ops --url http://host.openshell.internal:8790/mcp --transport streamable-http --timeout 600 --no-probe; openclaw mcp tools ops --include self_heal; openclaw mcp reload'
nemoclaw edge-agent agent --agent main --session-id t1 -m "Reply with one word: ready"   # 走 sandbox → gateway → shim → TRT-LLM
```

### 3.5 victim 沙箱（給錯誤注入用）

```bash
uv run --no-sync scripts/faults.py setup      # edge-victim (nginx, 127.0.0.1:8880) + edge-victim-app（上游）
uv run --no-sync scripts/faults.py verify     # / 200 /api/ 200 -> PASS
```

### 3.6（備援）vLLM 路線

```bash
uv run hf download Qwen/Qwen3.6-35B-A3B-FP8                 # 37 GB
docker stop trtllm-edge                                     # 兩顆大模型不能同時載
nohup scripts/serve.sh > logs/serve.log 2>&1 &              # :8100，MTP 投機解碼預設開
uv run --no-sync scripts/smoke.py
```

## 4. 日常操作

沒有任何服務開機自啟（使用者要求）。重開機後照這個順序：

```bash
cd ~/edge-agent
HOST=0.0.0.0 nohup scripts/serve-trt.sh > logs/trt-serve.log 2>&1 &      # 1. 推論
nohup uv run --no-sync scripts/oai_shim.py > logs/oai-shim.out 2>&1 &     # 2. 相容層
nohup uv run --no-sync scripts/ops_api.py  > logs/ops-api.out  2>&1 &     # 3. 工具 API
nemoclaw edge-agent start                                                  # 4. sandbox（若停了）
uv run --no-sync scripts/faults.py setup                                   # 5. victim（若要做 drill）
# 檢查
curl -sf http://127.0.0.1:8000/health && echo trt-ok; nemoclaw edge-agent status | grep Inference
# 停
docker stop trtllm-edge; pkill -f oai_shim.py; pkill -f ops_api.py; nemoclaw edge-agent stop
```
Log 位置：`logs/trt-serve.log`（引擎）、`logs/oai-shim.log`（`SHIM_DEBUG=1` 時記錄訊息形狀與上游 400）、`logs/ops-api.log`（每一次工具呼叫：來源、工具、參數、結果）、`logs/agent/*.jsonl`（每次自我修復的逐步決策）、`nemoclaw edge-agent logs -n 200`（sandbox 內 gateway）。

## 5. 我們做過的測試與數據

### 5.1 引擎 / 模型選型矯陣（`docs/bench-2026-09-22.md`）

用 `scripts/try.sh` 對每個組合起臨時 server 跑 `bench.py`、`probe.py`、`smoke.py`：

| 配置（vLLM 0.29，32k ctx，8 seqs） | 單流 tok/s | 8 併發合計 | 12.9k prefill | needle |
|---|---|---|---|---|
| Qwen3-8B BF16 | 14.1 | 116.6 | – | 8/8 |
| Qwen3.8-27B-NVFP4 | 11.6 | 82.4 | 2314 tok/s | 8/8 |
| Qwen3.8-27B-NVFP4 + MTP5 | 23.9 | 140.4 | 2125 | 8/8 |
| Qwen3.8-27B-NVFP4 + DFlash2 | 32.6 | 158.3 | 2278 | – |
| Qwen3.6-35B-A3B-FP8 | 48.9 | 269.8 | 3675 | 8/8 |
| **Qwen3.6-35B-A3B-FP8 + MTP3**（vLLM 正式） | **66.6** | **306.5** | 3353 | 8/8 |
| Qwen3.6-35B-A3B-FP8 + DFlash | 56.1 | 124.7 | 3412 | – |

結論：Spark 是帶寬瓶頸 → **MoE 小 active + 內建 MTP** 是甜蜜點；KV cache fp8 只增容量不加速；外掛 DFlash 在 35B 上輸給 MTP。

### 5.2 正式 server 標準指標（`docs/serving-metrics-2026-09-22.md`，`vllm bench serve`）

vLLM + Qwen3.6-35B + MTP，真實 IT 文字：短問答 TTFT 156 ms、TPOT 16 ms（62 tok/s）；8 併發 199 tok/s；3k-token RAG TTFT 416 ms；12.9k-token RAG 冷 3.3 s / prefix cache 命中 0.37 s；`--max-num-seqs 8` 是併發上限（16 併發時 TTFT 排隊到 16 s）。

### 5.3 兩個引擎對照（同一台、同一套腳本）

| | vLLM + Qwen3.6-35B-A3B-FP8 + MTP3 | **TensorRT-LLM + Nemotron-3-Nano-30B-A3B-NVFP4（正式）** |
|---|---|---|
| 啟動到 health | 275 s | 106 s |
| 單流 / 8 併發 | 60.7 / 343 tok/s | 57.9 / 237 tok/s（無投機解碼） |
| 12.9k prefill | 3.5 s（cache 命中 0.3 s） | 1.17 s（無 prefix cache） |
| GSM8K-CoT（150 題，thinking） | 92.7 % | **96.7 %** |
| IFEval prompt-strict（100 題） | 78.0 % | **80.0 %** |
| needle 探針（250 條相似 runbook 抓細節） | **8/8** | 5–7/8 |
| GPU 記憶體 | 52 GB | 34 GB |

品質評測指令（lm-eval-harness 打 OpenAI 相容端點）：
```bash
uv run lm_eval --model local-chat-completions --model_args "model=edge-agent,base_url=http://127.0.0.1:8000/v1/chat/completions,num_concurrent=8,tokenized_requests=False" \
  --tasks gsm8k_cot_llama --num_fewshot 8 --limit 150 --apply_chat_template --fewshot_as_multiturn --gen_kwargs "temperature=0,max_gen_toks=3072"
uv run lm_eval --model local-chat-completions --model_args "…同上…" --tasks ifeval --limit 100 --apply_chat_template --gen_kwargs "temperature=0,max_gen_toks=4096"
```

### 5.4 TensorRT-LLM 調參

PDL 無感；NGram 投機解碎觸發 `CUDA error: device-side assert`；`enable_block_reuse` 讓首個長 prompt 從 1.2 s 變 18.7 s → 全部不採用，正式用 `trt/nano.yaml`（`free_gpu_memory_fraction 0.5`、chunked prefill、`max_seq_len 32768`）。

## 6. 錯誤注入與自我修復 drill

兩條路徑都對同一組 victim 容器與同一套白名單工具。

**A. repo 內 agent 迴圈（看得到每一步決策）**
```bash
uv run --no-sync scripts/faults.py run bad-config      # reset → inject → agent.py → verify → PASS/FAIL
```
結果（TRT-LLM 路徑，thinking 開）：nginx-stopped 6 步 55 s、bad-config 7 步 70 s（讀 log 找到 `[emerg]` → 重寫設定 → restart）、upstream-down 5 步 60 s，**3/3 PASS**。軌跡：`logs/agent/*.jsonl`。

**B. 透過 NemoClaw sandbox agent**
```bash
scripts/nemoclaw-drill.sh bad-config        # 注入 → nemoclaw edge-agent agent … → verify → 印 ops-api.log
```
結果：bad-config 2.5 分、upstream-down 2 分、nginx-stopped 3 分，**3/3 PASS**。agent 依 prompt 在殼層執行 `/sandbox/bin/ops call self_heal '{}'`（host 上跑 agent.py 迴圈並回傳步驟與結論），再用 `check_health` 確認，最後回報 root cause / actions / final health。

## 7. 新增你自己的錯誤注入案例

1. `scripts/faults.py` 的 `inject()` 加一個分支（例如塞滿 tmpfs、改壞 upstream 名稱、殺掉 DNS）。
2. `runbooks/` 加一篇對應的 runbook（RAG 語料）。
3. 若需要新動作，在 `scripts/agent.py` 的 `TOOLS` + `run_tool()` 加白名單工具（ops API 與 MCP 會自動曝露）。
4. `uv run --no-sync scripts/faults.py run <case>` 與 `scripts/nemoclaw-drill.sh <case>` 各跑一次。
要動 Spark 宿主機的 systemd/docker，把 `run_tool` 的容器白名單換成 host 執行器即可，迴圈與驗證不用改。

## 8. 踩過的坑（一定要知道）

| 坑 | 症狀 | 解 |
|---|---|---|
| 系統 python3.12 沒 headers | Triton JIT `fatal error: Python.h` | uv-managed CPython（pyproject `python-preference=only-managed`） |
| nvcc 不在 PATH | vLLM 判 FlashInfer 不可用，NVFP4 模型死在第一個 decode | `CUDA_HOME=/usr/local/cuda`，PATH 加 `/usr/local/cuda/bin` |
| FlashInfer 首次 JIT 開 ~20 個 nvcc | unified memory 無 swap → 整台 livelock 2.5 小時 | `MAX_JOBS=4 FLASHINFER_NVCC_THREADS=4`；JIT 結果留在 `~/.cache/flashinfer` |
| kubelet image GC | 磁碟 >85% 時刪掉沒在用的 image（TRT-LLM 35 GB 被刪） | 退出 k8s；磁碟壓在 80% 下 |
| TRT-LLM + Omni 模型 | `PreTrainedTokenizerFast has no attribute tokenizer` | 用純文字版 Nemotron |
| TRT-LLM 離線載 tokenizer | `Failed to load hf tokenizer … Cannot reach huggingface.co` | `--tokenizer <本機 snapshot 路徑>` |
| TRT-LLM 預設 `max_num_tokens 8192` | 12.9k prompt 被拒 | YAML `enable_chunked_prefill: true`、`max_seq_len: 32768` |
| NemoClaw 安裝器在 Spark 自動 express onboard | 開始下載 35 B 模型、要起 managed vLLM :8000 | 預設 `NEMOCLAW_PROVIDER=custom …` 環境變數；跑掉就 kill |
| Sandbox 預設無 policy | `inference.local` 403 `policy_denied` | `policy add local-inference` |
| loopback 端點被改寫到 Ollama auth proxy :11435 | 401 | provider `OPENAI_BASE_URL=http://host.openshell.internal:8001/v1`，server 綁 0.0.0.0 |
| trtllm-serve 嚴格驗證 OpenClaw 訊息 | 400 `string_type`，OpenClaw 顯示 `Message ordering conflict` | `oai_shim.py` |
| 小模型走 OpenClaw meta 工具層 | id 打錯、arguments 空、字串 `\n` 雙重轉義 | 伺服端容錯；prompt 直接給殼層指令；MCP 只留 `self_heal` |
| `nemoclaw upload` 目的地 | 把檔名路徑當目錄 | 目的地給目錄 |
| `openshell sandbox exec` / `nemoclaw exec` 吃 stdin | heredoc 腳本後半段被吞 | 一律 `</dev/null` |

## 9. Hackathon 當天流程

`docs/hackathon-runbook.md`：離線 bundle 打包（`docker save` 容器 + HF cache tar）、`scripts/bootstrap-spark.sh` 各 stage 時間、Jetson AGX Thor / Orin / Orin Nano 的差異與備案模型、當天判斷指令。

## 10. 安全與待辦

- TRT-LLM API 綁 0.0.0.0 無認證（NemoClaw 直連需要）：lab LAN 可達，要收就用 iptables 只放 172.24.0.0/16。
- `~/.nemoclaw.bak-2026-09-22/credentials.json` 內有舊的 NVIDIA API key；`hsnl` 密碩曾出現在對話中 → 都建議更換。
- HF cache 還留著 ~35 GB 未完成的 `nvidia/Qwen3.6-35B-A3B-NVFP4`（NemoClaw express 誤下載），`hf cache rm model/nvidia/Qwen3.6-35B-A3B-NVFP4` 可清。
- 待做：RAG 真正的檢索器（Nemotron 在整包語料塞 prompt 時抓細節較弱）、host 級別動作的執行器、更多錯誤注入案例。

## 11. 文件索引

| 文件 | 內容 |
|---|---|
| `docs/superpowers/specs/2026-09-21-edge-agent-infra-design.md` | Phase 1 設計與地雷清單 |
| `docs/spark-hw.md` | 主機事實、port、停掉/移除了什麼、安全備註 |
| `docs/bench-2026-09-22.md` | 模型/加速矯陣、needle 探針、事故紀錄 |
| `docs/serving-metrics-2026-09-22.md` | TTFT/TPOT/ITL/E2E、GSM8K/IFEval、兩引擎對照 |
| `docs/plan-trtllm-nemoclaw.md` | TRT-LLM 與 NemoClaw 遷移：事實查核、執行日誌、每個坑的修法 |
| `docs/agent-design.md` | Agent 迴圈設計、工具白名單、兩條 drill 路徑與結果 |
| `docs/hackathon-runbook.md` | 當天流程、離線 bundle、Jetson 對照 |

Repo 副本：GitHub `Vanisherzd/dgx-spark-edge-agent`（private）、lab Spark `hsnl@192.168.2.60:~/edge-agent`（可直接 push）、筆電 `~/Desktop/HSNL/edge-agent`。lab VPN 下筆電連不到 GitHub，推 GitHub 從 Spark 端執行。
