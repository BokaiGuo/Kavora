# 指标说明（Metric Spec）

本文档对应当前仓库实现，重点覆盖两类语义：

- exporter 暴露的累计快照指标
- 实验窗口内计算的 run-local 指标

相关实现：

- `exporter/prometheus_parse.py`
- `exporter/adapters/*`
- `benchmark/collect.py`
- `benchmark/window_metrics.py`
- `scripts/run_reuse_experiment.py`

---

## 1) Prometheus 聚合策略（label-aware）

后端 exposition 常包含 labels（如 `model_name`、`engine`、`gpu`）。当前实现将其聚合为 `dict[str, float]` 的规则：

1. 解析：`prometheus_client.parser.text_string_to_metric_families`
2. 聚合：对 `counter / gauge / unknown` 按同名 `sample.name` 求和
3. 跳过：`histogram / summary` 不进入扁平 map
4. 过滤：支持 `METRIC_LABEL_FILTER`（`k=v,k2=v2`）或函数参数 `label_filter`

回退路径：当标准解析失败时，走按行宽松解析；当前 fallback 也会继续应用 `label_filter`，避免多 model / 多实例场景下把不相干 series 聚到一起。

这套聚合同时用于：

- exporter adapter 解析 backend `/metrics`
- benchmark 侧抓取 backend / exporter 快照

---

## 2) 前缀缓存计数兼容

### vLLM

`exporter/adapters/vllm.py` 兼容以下命名：

- 新版优先：`vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`
- 旧版回退：`vllm:prefix_cache_hits` / `vllm:prefix_cache_queries`

### SGLang

`exporter/adapters/sglang.py` 兼容以下命名变体：

- `sglang:prefix_cache_hits_total`
- `sglang_prefix_cache_hits_total`
- `sglang:prefix_cache_hits`
- `sglang_prefix_cache_hits`

以及对应的 `queries` 指标。

另外，SGLang 当前还兼容一组 token 级 fallback：

- `sglang:cached_tokens_total`
- `sglang_prompt_tokens_total` / `sglang:prompt_tokens_total`

这组 fallback 主要用于形成“方向上可用”的 hit ratio，但它和 vLLM / canonical prefix query counters 不是严格同义。

对容量类指标还需要补一条单位说明：

- 若直接拿到 `sglang:kv_total_blocks` / `sglang_obs:kv_active_blocks`，当前按 blocks 语义直接使用
- 若只能拿到 `sglang:max_total_num_tokens` / `sglang:num_used_tokens`，当前会按 `KVCACHE_TOKENS_PER_BLOCK`（默认 `16`）换算为 blocks，再写入 `kvcache_kv_*_blocks`

---

## 3) 两类命中率语义

### A. 累计快照语义：`kvcache_kv_cache_hit_ratio`

这是 exporter 暴露的累计快照指标：

- 定义：`prefix_hits / prefix_queries`
- 来源：单次 scrape 时看到的累计 counter
- 输出位置：exporter `/metrics`

适用场景：

- 在线观测
- Prometheus / Grafana 展示
- 快速肉眼判断后端前缀缓存是否在工作

不适用场景：

- 直接作为某一轮实验的 run-local 命中率

原因是它本质上是“某个时刻的累计比例”，可能混入预热、历史请求或前几轮实验。

### B. 实验窗口语义：`cache_hit_ratio_window`

这是当前实验链路推荐消费的 run-local 指标：

- 定义：`prefix_hits_delta / prefix_queries_delta`
- 其中：
  - `prefix_hits_delta = backend_after - backend_before`
  - `prefix_queries_delta = backend_after - backend_before`
- 计算位置：`benchmark/window_metrics.py`
- 输出位置：`summary.json -> runs[*].derived_window_metrics.cache_hit_ratio_window`

适用场景：

- `planner.compare_baseline`
- `scripts/plot_threshold_curve.py`
- `scripts/run_capacity_sweep.py`
- `summary.md` 中的实验对比
- final report 中的场景级对比表格

这也是当前仓库推荐的“实验结论语义”。

窗口计数器还有一个实现约定：

- 如果 `before` 快照抓取成功，但某个累计 counter 在该时刻尚未曝光
- 且同一 counter 在 `after` 快照里已经出现
- 当前会把 `before` 解释为 `0`

这个规则主要用于兼容 SGLang 一类“计数器首次增量后才开始出现在 `/metrics`”的后端，避免首轮实验被误判成 `missing`。

---

## 4) 窗口指标质量状态

### 在线负载信号

Observer 还将后端负载规范化为可缺失信号：

- `queue_depth`：vLLM 优先读取 `vllm:num_requests_waiting`；SGLang 读取兼容的 queue gauge
- `running_requests`：当前正在执行的请求数

对应 exporter 指标为 `kvcache_backend_queue_depth` 和 `kvcache_backend_running_requests`。原始后端未暴露这些 gauge 时，backend-state 将其标为 `missing`，load-aware 路由必须静态回退，不能把缺失解释成零队列。

窗口命中率除了数值本身，还有质量状态：

- `metric_quality = ok`
  - 成功拿到窗口前后 counter，并形成有效窗口命中率
- `metric_quality = missing`
  - 前后快照缺失，或无法形成有效 delta
- `metric_quality = stale`
  - 保留给旧版 / 非 backend-window 语义的兼容状态；当前主链路通常不会把 backend window hit ratio 标成 stale
- `metric_quality = mixed`
  - 多轮 run 中同时出现了 `ok` 与 `missing/stale`，说明有部分 run 可用，但整体不可视作“完全可比”

对应字段位置：

- `summary.json -> runs[*].derived_window_metrics.metrics_missing`
- `summary.json -> runs[*].derived_window_metrics.metrics_stale`
- `summary.json -> runs[*].quality.metric_quality`
- `summary.json -> runs[*].quality.hit_ratio_source`
- `summary.json -> aggregates.*.quality_summary.ok_metric_run_fraction`
- `baseline_compare.json -> scenarios[*].metric_quality`
- `baseline_compare.json -> scenarios[*].hit_ratio_source`
- `baseline_compare.json -> scenarios[*].ok_metric_run_fraction`
- `baseline_compare.json -> scenarios[*].num_runs_window_hit_ratio`
- `baseline_compare.json -> scenarios[*].num_runs_snapshot_fallback_hit_ratio`
- `threshold_recommended_rps_curve.csv -> metric_quality`
- `threshold_recommended_rps_curve.csv -> num_runs_ok_metrics`
- `threshold_recommended_rps_curve.csv -> hit_ratio_source`
- `capacity_sweeps/.../summary.json -> points[*].aggregates.quality_summary`
- `summary.md -> prefix_check / strict runs / token fallback runs`
- final report tables -> `prefix_check / strict runs / token fallback runs`

注意：

- `cache_hit_ratio_window = 0.0` 表示“明确测得为 0”
- `cache_hit_ratio_window = null` 表示“缺失或无法形成有效窗口值”
- 当前 backend-window 语义里，`queries_delta <= 0` 也会判为 `missing`，而不是回退成 exporter 累计快照
- `metric_quality = ok` 现在表示“该场景下所有 run 的命中率质量都为 ok”，不再是“只要有一个 ok 就算 ok”
- `before` 未曝光但 `after` 已曝光且抓取无报错，不会直接判成 `missing`，而是按累计 counter 的 `0 -> after` 处理

这两者不能混为一谈。

---

## 5) Exporter 对外关键指标

### 业务派生

- `kvcache_kv_cache_hit_ratio`
- `kvcache_kv_hidden_reuse_ready_perc`
- `kvcache_kv_effective_residency_perc`
- `kvcache_kv_cold_free_perc`

### 自监控

- `kvcache_exporter_scrape_last_success_timestamp_seconds`
- `kvcache_exporter_scrape_consecutive_failures`
- `kvcache_exporter_scrape_failures_total`
- `kvcache_exporter_prefix_metric_semantics_info`
- `kvcache_exporter_prefix_metric_comparable`
- `kvcache_exporter_prefix_metric_token_fallback`

其中：

- 业务派生更偏“服务状态快照”
- 自监控更偏“这个快照现在还能不能信”
- prefix 语义自检指标更偏“这个 hit ratio 在跨后端 / 跨版本比较时到底有多严格可比”

prefix 语义自检的推荐读法：

- `kvcache_exporter_prefix_metric_comparable = 1`
  - 表示当前 hit ratio 来自严格可比的 prefix query counters
- `kvcache_exporter_prefix_metric_token_fallback = 1`
  - 表示当前 hit ratio 来自 token counters fallback，例如 `cached_tokens_total / prompt_tokens_total`
- `kvcache_exporter_prefix_metric_semantics_info`
  - 会带上 `semantics`、`comparability`、`basis`、`hits_metric`、`queries_metric`，用于明确说明当前 exporter 实际消费的是哪组指标

---

## 6) 当前语义边界

当前仓库未默认打 `vllm_obs` block 语义补丁时：

- `kvcache_kv_hidden_reuse_ready_perc` 可能长期接近 0
- block 语义相关指标在不同后端上的完备度仍可能不同

另外需要明确：

- exporter 的 `kvcache_kv_cache_hit_ratio` 仍然保留，主要用于在线观测
- 实验规划逻辑已经优先切换到窗口命中率 `cache_hit_ratio_window`
- 当输入是旧版实验产物时，planner 仍可能回退消费 exporter 快照命中率；当前会在产物中显式标记 `hit_ratio_source = snapshot_fallback`
- 当后端只暴露 usage ratio、未暴露总 block 数时，exporter 会保留 ratio 类指标，但不再伪造 `total_blocks=100` 一类绝对容量值
- 当 `kvcache_exporter_prefix_metric_token_fallback = 1` 时，更适合把对应 hit ratio 视为“方向性信号”，而不是拿来做严格的跨后端绝对比较

---

## 7) Capacity Sweep 排名字段

`scripts/run_capacity_sweep.py` 会在 sweep 产物中补一层 ranking 语义，用来把“原始点位”转换成“建议结论”。

关键字段：

- `points[*].ranking.is_feasible`
  - 含义：该点满足 hard constraints，对应 `baseline_hard_only_recommended_rps > 0`
- `points[*].ranking.is_best_safe_candidate`
  - 含义：该点满足 dual boundary 约束，对应 `dual_boundary_recommended_rps > 0`
- `ranking.by_scenario.<scenario>.highest_feasible_point`
  - 含义：该场景下满足 hard constraints 的最高吞吐点
- `ranking.by_scenario.<scenario>.best_safe_point`
  - 含义：该场景下满足 dual boundary 的保守推荐点
- `ranking.by_scenario.<scenario>.ranked_points`
  - 含义：按 `req_s_mean` 优先、再按延迟与并发信息排序后的点位列表

配套图表：

- `capacity_sweep_ranking.png`
  - 蓝线：各并发点 `req_s mean`
  - 绿方块：`highest_feasible_point`
  - 红星：`best_safe_point`

---

## 8) 当前推荐读取顺序

如果你的目标是“看服务状态”，优先看：

1. exporter `/metrics`
2. `kvcache_kv_cache_hit_ratio`
3. exporter freshness 自监控

如果你的目标是“下实验结论 / 做推荐 RPS”，优先看：

1. `summary.json -> runs[*].derived_window_metrics`
2. `baseline_compare.json`
3. `threshold_recommended_rps_curve.csv`
4. `capacity_sweeps/.../summary.json -> ranking`
