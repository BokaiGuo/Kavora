# KV Cache Observability（本地离线版）

面向本地推理服务（vLLM / SGLang）的 KV cache 可观测与实验工具集，当前实现聚焦：

- 本地后端启动与停机（PID 文件管理）
- Exporter 抓取后端 `/metrics` 并输出 `kvcache_*`
- Custom HTTP 压测（可控复用负载）
- low/high reuse 对比实验与 baseline/dual 推荐值比较
- 阈值扫描可视化（含中文图与双子图）

---

## 当前代码范围（以仓库实现为准）

- `exporter/`：后端指标抓取、派生与 `/healthz` `/readyz` `/metrics`
- `benchmark/runner.py`：最小 custom HTTP 压测器（E2E 语义）
- `benchmark/collect.py`：带 label 聚合的 Prometheus 指标抓取
- `planner/compare_baseline.py`：hard-only vs dual-boundary 推荐值对比
- `scripts/run_reuse_experiment.py`：low/high reuse 重复实验
- `scripts/plot_threshold_curve.py`：阈值-推荐RPS曲线（总览 + 双子图 + 中文版）

> 说明：当前 `planner/build_frontier.py` 是轻量“输入汇总器”，不再是旧版复杂 frontier 规则引擎。

---

## 快速开始（本地）

- 统一命令文档：`docs/quickstart_local.md`（含 vLLM 与 SGLang 对等流程）
- 推荐启动顺序：`scripts/launch_vllm.sh` / `launch_sglang.sh` → `scripts/wait_backend_ready.sh` → `scripts/launch_exporter.sh`（避免 exporter 早于后端就绪；exporter 使用 `results/exporter.pid` 便于停机）
- 一键入口：
  - vLLM：`BACKEND=vllm bash scripts/experiment_template_local.sh`
  - SGLang：`BACKEND=sglang bash scripts/experiment_template_local.sh`
  - 或直接：`bash scripts/experiment_template_local_sglang.sh`

---

## 文档索引

- `docs/README.md`
- `docs/quickstart_local.md`
- `docs/metric_spec.md`
- `docs/benchmark_protocol.md`
- `docs/limitations.md`
- `报告.md`
