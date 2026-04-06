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
- dual/baseline 分离效果高度依赖 `min_hit_ratio` 阈值，属于策略参数，不是物理常数。

## 中文图字体依赖

`scripts/plot_threshold_curve.py` 会优先尝试系统 CJK 字体（如 Noto CJK / Droid fallback）。若环境缺字体，中文图可能显示为方块；建议在系统安装 Noto CJK 字体包。
