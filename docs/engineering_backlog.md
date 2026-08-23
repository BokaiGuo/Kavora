# 工程化 Backlog（当前状态）

本文档只保留**尚未完成或需要持续推进**的任务；已落地项不再作为 TODO。

---

## 已落地（与当前实现一致）

- Prometheus label-aware 解析与聚合：`exporter/prometheus_parse.py`
- vLLM 新旧前缀计数兼容：`exporter/adapters/vllm.py`
- Exporter `/healthz` + `/readyz` 分离与 scrape 自监控指标
- Custom HTTP 压测 E2E 语义与可复现种子
- low/high reuse 实验模板与阈值曲线脚本
- `strict / estimated / fallback / missing` 后端证据语义对齐
- SLO-aware `AutoCalibrator` 阈值与并发推荐
- 匿名 workload signature replay 与人工 canary 审批门禁
- Decision + Outcome Ledger、append-only JSONL 与重启恢复
- outcome-grounded TTFT predictor 拟合、prediction calibration 与 drift gate
- vLLM 原生 KV-event ZMQ subscriber、gap replay、dedupe 与 generation reset
- 多策略 replay policy laboratory
- vLLM request-to-external-block-hash resolver 与 exact longest-prefix placement
- switchback / isolated-pool ExperimentController、实验账本和 policy evaluation report
- held-out TTFT predictor validation 与 experiment-aware lifecycle gate

---

## 仍需推进（P1/P2）

| 优先级 | 任务 | 说明 |
|---|---|---|
| P1 | `vllm_obs` block 语义补丁接入 | 让 `hidden_reuse_ready_perc` 从“弱信号”变成“可判别信号” |
| P1 | 报告自动化生成 | 一次命令产出 markdown + 图 + 结论段落 |
| P2 | CI 补充图表回归 | 已加入 `scripts/check_capacity_sweep_artifact.py`，检查 summary schema、Markdown 图引用和 PNG 可读性 |
| P1 | 真实 GPU Stage 2 artifact | 补齐硬件、模型和原始请求级证据，作为 v0.1.0 发布门禁 |
| P2 | SGLang 原生事件 transport | 在 SGLang 提供稳定事件协议后接入相同 sequence/generation contract |
| P1 | Stage 7 online experiment artifact | 在真实请求上冻结 switchback 或 isolated-pool assignment、outcome journal 与 causal report |
| P2 | held-out predictor evidence | 使用独立真实 GPU 时间窗运行已实现的 validator，并冻结泛化误差 artifact |

---

## 面试追问建议回答（更新版）

- **为什么 dual 会在高阈值突然掉到 0？**  
  因为当前实现按“是否满足阈值”硬筛通过样本，超过阈值后无可行点时推荐值归零；这体现了风险偏好而非模型故障。

- **为什么 high_reuse 的拐点更靠右？**  
  因为其命中率分布整体更高，在更严格阈值下仍有样本可通过。

- **为什么 `hidden_reuse_ready_perc` 目前不敏感？**  
  当前没有默认启用 block 语义补丁，缺少可复用块真值信号。
