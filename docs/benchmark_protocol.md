# 压测协议（Benchmark Protocol）

本文档描述当前仓库在本地离线实验中的压测字段语义，以及从“累计快照”升级到“窗口指标”后的推荐消费方式。

---

## 1) 延迟字段语义

在 custom HTTP 路径中，当前统计的是请求端到端耗时（E2E），因此输出字段为：

- `latency.e2e_latency_p95_ms`
- `latency.e2e_latency_mean_ms`

并将以下字段保留为可选空值，避免“伪精确”：

- `latency.ttft_p95_ms = null`
- `latency.tpot_p95_ms = null`

如果后续接入真正的流式 token 级观测，再把 TTFT/TPOT 填成真实值。

---

## 2) Warmup 与 Measure 分段

当前 `benchmark.runner.run_custom_http()` 支持：

- `warmup_requests`
- `num_requests`

语义如下：

- `warmup_requests`：预热请求，不计入最终吞吐与延迟统计
- `num_requests`：正式测量请求，进入最终 `summary.requests`、`summary.latency`、`summary.throughput`

对应输出字段：

- `summary.warmup`
- `summary.timing.run_started_ts`
- `summary.timing.warmup_end_ts`
- `summary.timing.run_finished_ts`

这样做的目的，是把冷启动、首次缓存建立等因素从正式测量窗口里剥离出来。

---

## 3) 可复现性

每个请求使用确定性种子：

- `request_seed = sha256(f"{base_seed}:{request_id}")[:16]`

输出中会写入：

- `reproducibility.base_seed`
- `reproducibility.request_seed_strategy`
- `reproducibility.prompt_digest_sha256`

这样可以区分“系统噪声导致波动”与“请求集变化导致波动”。

---

## 4) 实验窗口与快照时序

当前主链路的单轮时序为：

1. 抓 `backend_before`
2. 抓 `exporter_before`
3. 执行 warmup
4. 执行正式 measure
5. 抓 `backend_after`
6. 抓 `exporter_after`
7. 计算窗口指标

对应字段位置：

- `runs[*].window`
- `runs[*].metric_snapshots.backend_before`
- `runs[*].metric_snapshots.backend_after`
- `runs[*].metric_snapshots.exporter_before`
- `runs[*].metric_snapshots.exporter_after`
- `runs[*].derived_window_metrics`

这里的关键变化是：

- 旧逻辑更接近“实验结束后抓一次 exporter 快照”
- 新逻辑明确记录了窗口前后边界，并允许基于 delta 计算 run-local 指标

---

## 5) 从累计快照到窗口指标

### 旧语义

旧版实验结论常直接读取：

- `exporter_metrics.kvcache_kv_cache_hit_ratio`

这本质上是 exporter 在某一时刻暴露的累计比例，适合观测，但不严格对应某一轮实验。

### 新语义

当前实验推荐优先读取：

- `derived_window_metrics.cache_hit_ratio_window`

它来自：

- `prefix_hits_delta / prefix_queries_delta`

也就是窗口前后 backend counter 的差值。

这使得 planner 和阈值扫描消费的命中率，尽量与当前 run 的负载窗口对齐。

---

## 6) 与 planner 的关系

当前 planner 与阈值扫描推荐优先读取：

- `summary.latency.e2e_latency_*`
- `summary.requests.*`
- `summary.throughput.req_s`
- `derived_window_metrics.cache_hit_ratio_window`

并结合质量状态：

- `metrics_missing`
- `metrics_stale`

当命中率缺失或陈旧时：

- baseline 路径仍可基于延迟和成功率工作
- dual-boundary 路径会把该 run 视为不满足命中率约束

补充说明：

- 当前主链路的 run-local 命中率来自 backend `before/after` counter delta，因此 exporter freshness 不再否定一个已经形成的 backend window
- `metrics_stale` 字段保留用于兼容旧产物或其他非 backend-window 语义；当前本地离线主链路更常见的是 `ok / missing`
- 聚合层如果只有部分 run 拿到了有效命中率，现在会显式标记成 `metric_quality = mixed`，并通过 `num_runs_ok_metrics` / `num_runs` 暴露覆盖度
- 同时会把 exporter prefix 自检汇总成 `prefix_check / strict runs / token fallback runs`，直接出现在 `summary.md` 和 final report 表格里

---

## 7) 当前仓库主流程（low/high reuse，vLLM / SGLang）

当前推荐实验流程：

1. 选择后端（vLLM 或 SGLang），通过 `scripts/experiment_template_local.sh` 触发实验
2. `scripts/run_reuse_experiment.py` 跑 `low_reuse` 与 `high_reuse`
3. 输出包含窗口快照与窗口命中率的 `summary.json`
4. `planner.compare_baseline` 比较 hard-only 与 dual-boundary 推荐值
5. `scripts/run_capacity_sweep.py` 对并发点做 capacity sweep，并输出 ranking
6. `scripts/plot_threshold_curve.py` 对 `min_hit_ratio` 做阈值扫描并输出曲线

---

## 8) Capacity Sweep 协议

`scripts/run_capacity_sweep.py` 当前按 `scenario x concurrency_values x repeats` 组织实验。

每个 point 会输出：

- `points[*].runs`
- `points[*].aggregates`
- `points[*].recommendation`
- `points[*].ranking`

其中：

- `points[*].recommendation.baseline_hard_only_recommended_rps`
  - 表示只看 hard constraints 的推荐值
- `points[*].recommendation.dual_boundary_recommended_rps`
  - 表示同时满足 hard constraints 和 hit ratio 下界时的推荐值
- `points[*].ranking.is_feasible`
  - 表示该 point 至少满足 hard constraints
- `points[*].ranking.is_best_safe_candidate`
  - 表示该 point 通过 dual boundary，可进入保守推荐候选

场景级别还会输出：

- `ranking.by_scenario.<scenario>.highest_feasible_point`
- `ranking.by_scenario.<scenario>.best_safe_point`
- `ranking.by_scenario.<scenario>.ranked_points`

这样 sweep 产物不仅能回答“扫到了哪些点”，还能回答：

- 哪个点是该场景下的最高可行点
- 哪个点是更保守、更适合落地的 best safe point

配套图表：

- `capacity_sweep_ranking.png`

图中语义：

- 蓝线：所有 sweep point 的 `req_s mean`
- 绿方块：`highest_feasible_point`
- 红星：`best_safe_point`

---

## 9) 阈值扫描协议

推荐参数：

- `min_hit_ratio`: `0.65 ~ 0.82`，步长 `0.01`
- `e2e_p95_slo_ms`: `1500`
- `min_success_rate`: `0.99`
- `safety_factor`: `0.9`

主要产物：

- `threshold_recommended_rps_curve.csv`
- `threshold_recommended_rps_curve.json`
- `threshold_recommended_rps_curve.png`
- `threshold_recommended_rps_curve_split.png`
- 中文版本：
  - `threshold_recommended_rps_curve_zh.png`
  - `threshold_recommended_rps_curve_split_zh.png`

CSV / JSON 中会额外输出：

- `metric_quality`
- `num_runs`
- `num_runs_ok_metrics`
- `num_runs_missing_hit_ratio`
- `num_runs_stale_metrics`

这些字段用于区分“阈值没过”和“命中率数据本身不可靠”。
