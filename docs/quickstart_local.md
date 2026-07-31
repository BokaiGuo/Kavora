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

推荐直接使用仓库默认值启动：

```bash
bash scripts/launch_sglang.sh
```

`launch_sglang.sh` 默认会优先使用 `.venv-sglang/bin/python`，模型路径为 `models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0`，服务名为 `kvcache-local-tiny`，并启用这组本地稳定参数：

- `--enable-metrics`
- `--skip-server-warmup`
- `--mem-fraction-static 0.55`
- `--context-length 1024`

如果需要覆盖端口、模型或显存比例：

```bash
MODEL="/path/to/model" \
SGLANG_HOST="127.0.0.1" \
SGLANG_PORT="30000" \
SERVED_MODEL_NAME="kvcache-local-tiny" \
SGLANG_MEM_FRACTION="0.55" \
SGLANG_CONTEXT_LENGTH="1024" \
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

启动后建议确认 `curl -sf http://localhost:9108/readyz` 返回 `200`，而不只是 `/healthz` 活着。

### 3.2 对接 SGLang（localhost:30000/metrics）

```bash
KVCACHE_BACKEND_METRICS_URL="http://localhost:30000/metrics" \
KVCACHE_BACKEND_TYPE="sglang" \
KVCACHE_EXPORTER_HOST="localhost" \
KVCACHE_EXPORTER_PORT="9108" \
bash scripts/launch_exporter.sh
```

同样建议以 `/readyz` 作为“exporter 已拿到有效 scrape”的启动检查。

> 若你曾用旧文档里的裸 `nohup ... &` 启动 exporter 且**未**写入 `results/exporter.pid`，`stop all` 无法识别该进程；请改用上述脚本，或手动结束对应 `uvicorn exporter.app:app` 进程。

## 4) 跑完整实验流：reuse + baseline compare + capacity sweep + threshold curve + final report

`scripts/experiment_template_local.sh` 现在默认采用**隔离执行**：

- 在 `reuse experiment` 前会重启一次 serving stack
- 在 `capacity sweep` 中默认会在**每个点位前**重启一次 serving stack
- 目的：避免前序请求留下的 prefix cache 污染后续点位，使 `low_reuse` / `high_reuse` 的命中率更可信

这也带来两个使用约定：

- `MODEL` 代表**请求时使用的 served model name**，默认是 `kvcache-local-tiny`
- 如果你需要在隔离重启时覆盖真实模型路径，请使用 `BACKEND_MODEL_PATH` 或 `STACK_MODEL_PATH`，不要把模型路径直接传给 `MODEL`

如果你想关闭隔离模式、复用当前已经启动的后端实例：

```bash
ISOLATE_EXPERIMENT_STACK=0 bash scripts/experiment_template_local.sh
```

如果只想关闭 `capacity sweep` 的点位级重启，而保留主实验前重启：

```bash
ISOLATE_CAPACITY_SWEEP_POINTS=0 bash scripts/experiment_template_local.sh
```

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
SWEEP_OUT_DIR="results/capacity_sweeps/local_vllm_r3" \
SWEEP_CONCURRENCY_VALUES="1,2,4,8" \
SWEEP_REPEATS=3 \
BACKEND="vllm" \
bash scripts/experiment_template_local.sh
```

如果隔离重启时还要指定本地模型路径，可以额外传：

```bash
BACKEND_MODEL_PATH="/path/to/model" bash scripts/experiment_template_local.sh
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
SWEEP_OUT_DIR="results/capacity_sweeps/local_sglang_r3" \
SWEEP_CONCURRENCY_VALUES="1,2,4,8" \
SWEEP_REPEATS=3 \
BACKEND="sglang" \
bash scripts/experiment_template_local.sh
```

如果隔离重启时还要指定本地模型路径，可以额外传：

```bash
BACKEND_MODEL_PATH="/path/to/model" bash scripts/experiment_template_local.sh
```

默认会额外生成一份 capacity sweep 产物：

- `summary.json`
- `summary.md`
- `capacity_sweep_ranking.png`
- `ranking.by_scenario.*.highest_feasible_point`
- `ranking.by_scenario.*.best_safe_point`

同一条命令也会继续生成：

- `threshold_recommended_rps_curve.json`
- `threshold_recommended_rps_curve.png`
- `threshold_recommended_rps_curve_split.png`
- `final_report.md`
- `final_report_zh.md`

如果这次只想跑 reuse 实验，不跑 sweep：

```bash
RUN_CAPACITY_SWEEP=0 bash scripts/experiment_template_local.sh
```

如果只想关闭最终汇总报告：

```bash
RUN_FINAL_REPORT=0 bash scripts/experiment_template_local.sh
```

如果只想保留英文版、关闭中文版：

```bash
RUN_FINAL_REPORT_ZH=0 bash scripts/experiment_template_local.sh
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

## 6) 生成统一最终报告

把 reuse、baseline compare、threshold curve、capacity sweep 汇总成一份 markdown：

```bash
.venv/bin/python scripts/generate_final_report.py \
  --reuse-summary results/experiments/reuse_local_r5_final2/summary.json \
  --baseline-compare results/experiments/reuse_local_r5_final2/baseline_compare.json \
  --threshold-json results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve.json \
  --threshold-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve.png \
  --threshold-png results/experiments/reuse_local_r5_final2/threshold_recommended_rps_curve_split.png \
  --capacity-summary results/capacity_sweeps/local_vllm_r3/summary.json \
  --capacity-png results/capacity_sweeps/local_vllm_r3/capacity_sweep_ranking.png \
  --out-md results/final_report_vllm.md
```

## 7) 停止本地服务

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
