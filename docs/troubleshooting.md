# 故障排查（Troubleshooting）

本文档整理本仓库本地离线实验链路的常见故障定位方法，适用于以下主流程：

1. 启动后端（vLLM / SGLang）
2. 等待后端就绪
3. 启动 exporter
4. 运行 `scripts/experiment_template_local.sh`
5. 分析 `summary.json`、`baseline_compare.json` 与图表产物

---

## 5 分钟排障 Checklist

### 1) 先确认 backend 真能用

- 访问 backend 的 `/health`，确认不是“进程存在但服务未就绪”。
- 手动验证 `POST /v1/completions` 能返回，避免把“后端未 ready”误判成 exporter 或 planner 问题。
- 若这一步不通，先不要继续排查 exporter 与 planner。

相关入口：

- `scripts/launch_vllm.sh`
- `scripts/launch_sglang.sh`
- `scripts/wait_backend_ready.sh`

### 2) 再确认 exporter 是 ready，而不只是活着

- `GET /healthz` 返回 `200` 只表示 exporter 进程和 HTTP 栈可用。
- `GET /readyz` 返回 `200` 才表示最近一次 backend scrape 成功且未过期。
- 若 `healthz=200` 但 `readyz=503`，优先检查 `results/exporter.log`。

关键语义见：

- `exporter/app.py`
- `docs/limitations.md`

### 3) 直接看 exporter `/metrics` 是否有关键字段

优先确认以下指标是否存在且数值合理：

- `kvcache_kv_cache_hit_ratio`
- `kvcache_kv_hidden_reuse_ready_perc`
- `kvcache_exporter_scrape_consecutive_failures`
- `kvcache_exporter_scrape_failures_total`
- `kvcache_exporter_prefix_metric_comparable`
- `kvcache_exporter_prefix_metric_token_fallback`

如果只有 exporter 自监控指标、没有业务指标，通常说明 exporter 没有成功抓到 backend `/metrics`。

### 4) 看实验主产物 `summary.json`

优先检查：

- `runs[*].summary.requests.ok`
- `runs[*].summary.requests.failed`
- `runs[*].summary.latency.e2e_latency_p95_ms`
- `runs[*].summary.throughput.req_s`
- `runs[*].derived_window_metrics`
- `runs[*].metric_snapshots`

常见判断：

- `summary` 正常但 `derived_window_metrics.cache_hit_ratio_window = null`：先检查窗口快照和质量状态。
- `failed` 很高且 `e2e_latency_p95_ms` 很大：问题多半在 backend 响应或压测参数过重。

### 5) 看命中率字段是否真的有值

- 优先检查 `runs[*].derived_window_metrics.cache_hit_ratio_window`
- 再看：
  - `runs[*].derived_window_metrics.metrics_missing`
  - `runs[*].derived_window_metrics.metrics_stale`
- 若窗口命中率长期为 `null`，优先怀疑：
  - backend before/after 快照抓取失败
  - backend 没暴露对应前缀缓存 counter
  - counter reset 或窗口内 `queries_delta <= 0`

再辅助检查：

- `runs[*].exporter_metrics.kvcache_kv_cache_hit_ratio`
- `runs[*].metric_snapshots.exporter_after`

不要第一时间把“命中率看起来没值”解释成“缓存完全没有收益”。

### 6) 解读 planner 输出时先看数据是否完整

若出现：

- `baseline_hard_only_recommended_rps > 0`
- `dual_boundary_recommended_rps = 0`

通常表示：

- 延迟和成功率达标
- 但窗口命中率没达到阈值，或命中率数据缺失 / 陈旧

这时先回查：

- `summary.json -> runs[*].derived_window_metrics.cache_hit_ratio_window`
- `summary.json -> runs[*].derived_window_metrics.metrics_missing`
- `summary.json -> runs[*].derived_window_metrics.metrics_stale`

再判断是不是策略参数太严。

同时补看：

- `baseline_compare.json -> scenarios[*].hit_ratio_source`
- `threshold_recommended_rps_curve.csv -> hit_ratio_source`

如果看到：

- `hit_ratio_source = window`
  - 表示当前推荐逻辑基于窗口命中率语义
- `hit_ratio_source = snapshot_fallback`
  - 表示当前输入产物缺少窗口命中率，planner 回退消费 exporter 累计快照命中率
- `metric_quality = mixed`
  - 表示部分 run 有效、部分 run 缺失；此时可以做方向性判断，但不宜把均值直接当成“完整可比”的结论

这类回退不等同于错误，但说明当前结论更适合做方向性判断，不宜直接作为严格容量结论。

再补一条 prefix 语义自检：

- `kvcache_exporter_prefix_metric_comparable = 1`
  - 当前 hit ratio 来自严格可比的 prefix query counters
- `kvcache_exporter_prefix_metric_token_fallback = 1`
  - 当前 hit ratio 来自 token counters fallback，更适合做方向性判断

### 7) 最后再看图

- 图表只是 `summary.json` 的再加工。
- 如果 JSON 源数据本身不对，图再完整也只是把错误画出来。

---


## vLLM 重启窗口：exporter `503` / `ConnectError`

这是本仓库在 **隔离执行** 场景下曾经真实遇到过的一类问题，典型发生在：

- `scripts/experiment_template_local.sh` 开启隔离模式
- `capacity sweep` 在点位之间重启 vLLM
- exporter 刚跟着重启，但 backend 还处在旧实例退出 / 新实例未完全 ready 的窗口里

典型现象：

- exporter 日志里出现 `backend scrape failed`
- traceback 里有 `httpx.ConnectError` / `httpcore.ConnectError`
- `GET /readyz` 短暂返回 `503`
- `capacity sweep` 某些点位没结果，甚至 `high_reuse` 的 `highest_feasible_point` / `best_safe_point` 变成 `None`

### 旧根因

旧版本里，隔离重启有两个问题叠在一起：

1. `scripts/lib/pid_utils.sh` 在停 vLLM 时，给 PID 发了 `SIGTERM` 之后并没有真正等到旧进程退出。
2. 隔离重启环境还会把 `VLLM_HOST` / `VLLM_PORT` 这类无效环境变量传给 vLLM 进程，增加启动噪声。

结果就是：

- 旧 vLLM 还没完全退出时，`/health` 可能短暂还能返回 `200`
- `one_click_up.sh` 会过早认为 backend ready
- exporter 被过早拉起，于是在 restart window 里看到 `ConnectError` 和 `/readyz = 503`

### 当前修复状态

当前版本已经做了两层修复：

- `scripts/lib/pid_utils.sh`
  - 停机时会轮询 PID，真正等旧进程退出
  - 超时后会 `SIGKILL`
- 隔离重启逻辑
  - 不再把 `VLLM_HOST` / `VLLM_PORT` 注入 vLLM 子进程环境

修复后的真实验证里：

- restart window 的 exporter `ConnectError` 不再复现
- `/readyz 503` 的误判窗口也不再复现
- vLLM 的 `capacity sweep high_reuse` 能重新拿到可行点

### 如果你现在仍然看到这个问题，优先检查

1. 你当前代码是否包含最新的 `scripts/lib/pid_utils.sh` 停机等待逻辑。
2. 你是否真的在用隔离模式：
   - `ISOLATE_EXPERIMENT_STACK=1`
   - `ISOLATE_CAPACITY_SWEEP_POINTS=1`
3. 你是否混用了仓库 PID 管理脚本和手工启动的旧 vLLM 进程。
   - 如果旧进程不是通过 `results/*.pid` 管理的，`one_click_down.sh` 可能停不干净。
4. exporter 的 `readyz` 是否只是短暂 `503`，随后恢复；还是一直 `503`。
   - 短暂 `503` 更像 restart window
   - 长期 `503` 更像 backend 真没起来

### 最短排查命令

先做一次“停干净再重来”：

```bash
bash scripts/one_click_down.sh
```

然后确认没有残留旧 PID 文件：

```bash
ls results/*.pid
```

再重新启动实验，观察：

- backend `/health`
- exporter `/readyz`
- `results/exporter.log`

如果是 vLLM 专项排查，重点看：

- 是否在 `capacity sweep` 的**点位切换时**出现问题
- 是否只有 restart window 报错，但后续点位能恢复
- 是否最终 `summary.json` / `capacity sweep summary.json` 里缺了某个点位

### 什么时候可以判定“已经修好”

以下几条同时成立时，可以认为这个问题已经消失：

- restart 过程中 exporter 不再打印 `ConnectError`
- `/readyz` 不再在点位切换时反复掉到 `503`
- `capacity sweep` 的目标点位能完整产出
- `high_reuse` 不再无故丢失 `highest_feasible_point` / `best_safe_point`

## 常见现象对照表

| 现象 | 最可能原因 | 优先检查 |
|------|------------|----------|
| 后端进程在，但实验一开始就全失败 | backend 未 ready，`/v1/completions` 不可用 | backend `/health`、`scripts/wait_backend_ready.sh` |
| exporter 进程在，但没有有效业务指标 | exporter 活着，但 scrape backend 失败 | exporter `/readyz`、`results/exporter.log` |
| `/healthz` 为 200，但 `/readyz` 为 503 | exporter 自身活着，但最近 scrape 失败或已过期 | `exporter/app.py`、`docs/limitations.md` |
| `summary.json` 里 `summary` 有值，但 `cache_hit_ratio_window` 为 `null` | 实验压测成功，但窗口快照无法形成有效命中率 | `scripts/run_reuse_experiment.py`、`benchmark/window_metrics.py` |
| `hit_ratio_window` 一直为 `null` | backend before/after 抓取失败，或 counter 不可用 | `metric_snapshots.*`、backend `/metrics` |
| `hit_ratio_window = 0.0` | 命中率窗口值明确为 0，不等同于缺失 | `derived_window_metrics` |
| 首轮 run 没有历史流量，但 `hit_ratio_window` 仍有值 | `before` 抓取成功但累计 counter 尚未曝光；系统按 `before=0` 解释 | `benchmark/window_metrics.py`、`metric_snapshots.backend_before` |
| `metric_quality = stale` | 旧版 / 非 backend-window 语义里，freshness 判定不新鲜 | exporter `/metrics` 中 freshness 指标、`metric_snapshots.exporter_after` |
| `metric_quality = mixed` | 部分 run 缺失 hit ratio，部分 run 正常 | `num_runs_ok_metrics`、`num_runs_missing_hit_ratio`、`summary.json -> aggregates.*.quality_summary` |
| `hit_ratio_source = snapshot_fallback` | planner 正在消费旧版快照语义，而非窗口语义 | `baseline_compare.json`、`threshold_recommended_rps_curve.csv` |
| `kvcache_exporter_prefix_metric_token_fallback = 1` | exporter 正在使用 token counters fallback，hit ratio 更偏方向性信号 | exporter `/metrics`、`kvcache_exporter_prefix_metric_semantics_info` |
| `hidden_reuse_ready_perc` 长期接近 0 | block 语义指标缺失，或当前后端指标完备度不足 | `docs/limitations.md`、`exporter/derive/compute.py` |
| 吞吐很低、失败很多 | backend 超时、并发过高、请求过重 | `summary.json` 中 `requests` / `latency` / `throughput` |
| `baseline` 有值但 `dual` 全是 0 | 窗口命中率不达阈值，或命中率质量不是 `ok` | `planner/compare_baseline.py`、`summary.json` |

---

## 最短排查路径

建议按下面顺序排查，避免在错误层级上花时间：

1. 先看 backend `/health`
2. 再测 backend `/v1/completions`
3. 再看 exporter `/readyz`
4. 再看 exporter `/metrics`
5. 再看 `summary.json`
6. 最后看 `baseline_compare.json` 与图表

一句话记忆：

- `healthz` 活着，不代表有数据
- `summary` 正常，不代表窗口命中率正常
- `dual=0` 时先查 `cache_hit_ratio_window`

---

## `0.0`、`null`、`stale` 的区别

这是当前版本排障里最容易混淆的三种状态：

- `cache_hit_ratio_window = 0.0`
  - 表示窗口命中率明确测得为 0
- `cache_hit_ratio_window = null`
  - 表示无法形成有效窗口值
- `metric_quality = stale`
  - 表示旧版 / 兼容语义下 freshness 判定不新鲜
- `metric_quality = mixed`
  - 表示不是所有 run 都可比，读均值时一定要同时看 `ok runs`

建议排查顺序：

1. 先看 `cache_hit_ratio_window`
2. 再看 `metrics_missing`
3. 再看 `metrics_stale`
4. 最后再去解释 dual/baseline 的差异

---

## 关键文件与接口

最常用的定位入口：

- 日志：`results/exporter.log`
- exporter 健康：
  - `/healthz`
  - `/readyz`
- exporter 指标：
  - `/metrics`
- 主实验结果：
  - `results/experiments/.../summary.json`
  - `results/experiments/.../summary.md`
- planner 输出：
  - `results/experiments/.../baseline_compare.json`
  - `results/experiments/.../threshold_recommended_rps_curve.csv`

关键实现文件：

- `exporter/app.py`
- `exporter/adapters/vllm.py`
- `exporter/adapters/sglang.py`
- `exporter/derive/compute.py`
- `benchmark/runner.py`
- `benchmark/collect.py`
- `benchmark/window_metrics.py`
- `scripts/run_reuse_experiment.py`
- `planner/compare_baseline.py`
- `planner/policy.py`
