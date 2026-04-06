# 压测协议（Benchmark Protocol）

## 延迟字段语义（P1-2）

在 custom HTTP 路径中，当前统计的是请求端到端耗时（E2E），因此输出字段为：

- `latency.e2e_latency_p95_ms`
- `latency.e2e_latency_mean_ms`

并将以下字段保留为可选空值，避免“伪精确”：

- `latency.ttft_p95_ms = null`
- `latency.tpot_p95_ms = null`

如果后续接入真正的流式 token 级观测，再把 TTFT/TPOT 填成真实值。

## 可复现性（P1-3）

每个请求使用确定性种子：

- `request_seed = sha256(f"{base_seed}:{request_id}")[:16]`

输出中会写入：

- `reproducibility.base_seed`
- `reproducibility.request_seed_strategy`
- `reproducibility.prompt_digest_sha256`

这样可以区分“系统噪声导致波动”与“请求集变化导致波动”。

## 与 planner 的关系

planner 在消费 custom HTTP 结果时，应优先读取 `e2e_latency_*` 字段；若未来有真实 TTFT/TPOT，则再切换到 token 级边界。

## 当前仓库主流程（low/high reuse，vLLM / SGLang）

本仓库当前推荐实验流程：

1. 选择后端（vLLM 或 SGLang），通过 `scripts/experiment_template_local.sh` 触发实验  
2. `scripts/run_reuse_experiment.py` 跑 `low_reuse` 与 `high_reuse`（默认各 `repeats=5`）  
3. `planner.compare_baseline` 比较 hard-only 与 dual-boundary 推荐值  
4. `scripts/plot_threshold_curve.py` 对 `min_hit_ratio` 做阈值扫描并输出曲线

## 阈值扫描协议

推荐参数：

- `min_hit_ratio`: `0.65 ~ 0.82`，步长 `0.01`
- `e2e_p95_slo_ms`: `1500`
- `min_success_rate`: `0.99`
- `safety_factor`: `0.9`

主要产物：

- `threshold_recommended_rps_curve.csv`
- `threshold_recommended_rps_curve.png`
- `threshold_recommended_rps_curve_split.png`
- 中文版本：
  - `threshold_recommended_rps_curve_zh.png`
  - `threshold_recommended_rps_curve_split_zh.png`
