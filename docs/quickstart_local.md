# 本地快速开始（唯一命令源）

本文件作为 `README.md` 与 `报告.md` 的统一命令来源。  
后续若有命令变更，只维护这里。

---

## 1) 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev,offline]"
```

## 2) 启动本地后端（vLLM / SGLang）

### 2.1 vLLM

```bash
MODEL="/home/gbk/code/KVcacheexporter/models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0" \
SERVED_MODEL_NAME="kvcache-local-tiny" \
HOST="localhost" \
ENABLE_PREFIX_CACHING="true" \
bash scripts/launch_vllm.sh
```

**在启动 exporter 之前**，等待推理服务在 HTTP `/health` 上可用（应用层就绪；避免 exporter 过早轮询后端 `/metrics` 导致日志里出现 `backend scrape failed`）：

```bash
bash scripts/wait_backend_ready.sh vllm
```

大模型冷启动较慢时，可加大超时（秒，默认 120）：

```bash
WAIT_HTTP_MAX_S=600 bash scripts/wait_backend_ready.sh vllm
```

### 2.2 SGLang

```bash
MODEL="/home/gbk/code/KVcacheexporter/models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0" \
SGLANG_HOST="localhost" \
SGLANG_PORT="30000" \
PYTHON_FOR_SGLANG="python3" \
bash scripts/launch_sglang.sh
```

同样先等服务就绪再启 exporter：

```bash
bash scripts/wait_backend_ready.sh sglang
```

## 3) 启动 exporter（按后端切换）

使用 `scripts/launch_exporter.sh`：与 `scripts/lib/pipeline_common.sh` / `run_pipeline_local_offline_backend.sh` **共用** `results/exporter.pid`，标准输出与错误写入 `results/exporter.log`；支持 `bash scripts/launch_exporter.sh stop` 精确停止。

### 3.1 对接 vLLM（localhost:8000/metrics）

```bash
KVCACHE_BACKEND_METRICS_URL="http://localhost:8000/metrics" \
KVCACHE_BACKEND_TYPE="vllm" \
KVCACHE_EXPORTER_HOST="localhost" \
KVCACHE_EXPORTER_PORT="9108" \
bash scripts/launch_exporter.sh
```

### 3.2 对接 SGLang（localhost:30000/metrics）

```bash
KVCACHE_BACKEND_METRICS_URL="http://localhost:30000/metrics" \
KVCACHE_BACKEND_TYPE="sglang" \
KVCACHE_EXPORTER_HOST="localhost" \
KVCACHE_EXPORTER_PORT="9108" \
bash scripts/launch_exporter.sh
```

> 若你曾用旧文档里的裸 `nohup ... &` 启动 exporter 且**未**写入 `results/exporter.pid`，`stop all` 无法识别该进程；请改用上述脚本，或手动结束对应 `uvicorn exporter.app:app` 进程。

## 4) 跑 low/high reuse 对比实验（vLLM / SGLang 对等）

### 4.1 vLLM

```bash
MODEL="kvcache-local-tiny" \
BASE_URL="http://localhost:8000" \
EXPORTER_METRICS_URL="http://localhost:9108/metrics" \
REPEATS=5 \
NUM_REQUESTS=80 \
CONCURRENCY=4 \
INPUT_LEN=320 \
OUTPUT_LEN=48 \
TIMEOUT_S=45 \
BASE_SEED=42 \
OUT_DIR="results/experiments/reuse_local_r5_final2" \
BACKEND="vllm" \
bash scripts/experiment_template_local.sh
```

### 4.2 SGLang

```bash
MODEL="kvcache-local-tiny" \
BASE_URL="http://localhost:30000" \
EXPORTER_METRICS_URL="http://localhost:9108/metrics" \
REPEATS=5 \
NUM_REQUESTS=80 \
CONCURRENCY=4 \
INPUT_LEN=320 \
OUTPUT_LEN=48 \
TIMEOUT_S=45 \
BASE_SEED=42 \
OUT_DIR="results/experiments/reuse_local_sglang_r5" \
BACKEND="sglang" \
bash scripts/experiment_template_local.sh
```

## 5) 画阈值曲线（含中文图）

以下示例与 **4.1** 的 `OUT_DIR` 对齐；若你改用其他目录，请同步修改 `--input` 与各输出路径。

```bash
.venv/bin/python scripts/plot_threshold_curve.py \
  --input results/experiments/reuse_local_r5_final2/summary.json \
  --out-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve.png \
  --out-split-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve_split.png \
  --out-zh-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve_zh.png \
  --out-zh-split-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve_split_zh.png \
  --out-csv results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve.csv \
  --out-json results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve.json \
  --start 0.65 \
  --end 0.82 \
  --step 0.01 \
  --e2e-p95-slo-ms 1500 \
  --min-success-rate 0.99 \
  --safety-factor 0.9
```

## 6) 停止本地服务

一次性停止 **exporter + vLLM + SGLang**（与 `scripts/one_click_down.sh` 一致，均依赖 `results/*.pid`）：

```bash
bash scripts/run_pipeline_local_offline_backend.sh stop all
```

仅停止 exporter（例如后端仍要保留给其它实验）：

```bash
bash scripts/launch_exporter.sh stop
```

仅停止某个推理后端（并同时停止 exporter，与旧版 `run_pipeline_local_offline*.sh stop` 行为一致）：

```bash
bash scripts/run_pipeline_local_offline_backend.sh stop vllm
bash scripts/run_pipeline_local_offline_backend.sh stop sglang
```
