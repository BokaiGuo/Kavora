# 限制与假设（Limitations）

## Exporter 健康检查：存活 vs 就绪

- **`GET /healthz`**：仅表示 **进程与 HTTP 栈可用**，适合 Kubernetes **liveness**。即使后端 metrics 长期拉取失败，此处仍可能返回 `200`。
- **`GET /readyz`**：表示 **最近一次成功 scrape 未过期** 且 **连续失败次数未超阈**。不满足时返回 **503** 及 JSON 原因（`stale`、`too_many_failures`、`last_error` 摘要），适合 **readiness**。
- **可观测性**：Exporter 自身在 `/metrics` 中暴露 `kvcache_exporter_scrape_last_success_timestamp_seconds`、`kvcache_exporter_scrape_consecutive_failures`、`kvcache_exporter_scrape_failures_total`（见 `exporter/registry/prom_writer.py`）。Grafana 告警建议 **同时** 看后端业务指标与上述「新鲜度」指标，避免「曲线停滞仍以为服务正常」。

环境变量（前缀 `KVCACHE_`）：`scrape_stale_after_s`（默认 120）、`scrape_failures_not_ready`（默认 3）可调就绪语义。

## 当前实现边界

- 当前主链路是 **本地离线 custom HTTP 压测**，不是旧版 `vllm bench serve` 矩阵流水线。
- vLLM 与 SGLang 共享同一套实验框架，但指标完备度可能因后端实现差异而不同（尤其是 block 语义相关指标）。
- `planner/build_frontier.py` 目前是轻量输入汇总器，不等同于历史版本的复杂容量策略引擎。
- 未打 `vllm_obs` block 语义补丁时，`hidden_reuse_ready_perc` 可能缺乏判别力（接近 0）。
- 当后端只有 usage ratio、缺少总 block 数时，当前实现会保留 ratio 类派生值，但不会再伪造绝对 block 容量。
- SGLang 若只暴露 token 级容量指标，当前会按 `KVCACHE_TOKENS_PER_BLOCK`（默认 `16`）换算到 block 语义；若后端 block 大小与默认值不同，需要显式配置。
- SGLang 若 prefix hit ratio 退化到 `cached_tokens_total / prompt_tokens_total` 一类 token counters fallback，当前仍可形成方向性 ratio，但它不等同于严格的 prefix query counters；建议结合 `kvcache_exporter_prefix_metric_comparable` / `kvcache_exporter_prefix_metric_token_fallback` 一起解读。
- dual/baseline 分离效果高度依赖 `min_hit_ratio` 阈值，属于策略参数，不是物理常数。

## 中文图字体依赖

`scripts/plot_threshold_curve.py` 会优先尝试系统 CJK 字体（如 Noto CJK / Droid fallback）。若环境缺字体，中文图可能显示为方块；建议在系统安装 Noto CJK 字体包。
