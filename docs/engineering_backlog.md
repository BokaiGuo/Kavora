# 工程化 Backlog（当前状态）

本文档只保留**尚未完成或需要持续推进**的任务；已落地项不再作为 TODO。

---

## 已落地（与当前实现一致）

- Prometheus label-aware 解析与聚合：`exporter/prometheus_parse.py`
- vLLM 新旧前缀计数兼容：`exporter/adapters/vllm.py`
- Exporter `/healthz` + `/readyz` 分离与 scrape 自监控指标
- Custom HTTP 压测 E2E 语义与可复现种子
- low/high reuse 实验模板与阈值曲线脚本

---

## 仍需推进（P1/P2）

| 优先级 | 任务 | 说明 |
|---|---|---|
| P1 | `vllm_obs` block 语义补丁接入 | 让 `hidden_reuse_ready_perc` 从“弱信号”变成“可判别信号” |
| P1 | dual 阈值自动校准 | 给定目标 SLO 时自动建议 `min_hit_ratio` 区间 |
| P1 | 报告自动化生成 | 一次命令产出 markdown + 图 + 结论段落 |
| P2 | CI 补充图表回归 | 检查脚本产物文件存在、字段完整、无空图 |
| P2 | 真实流量回放 | 验证 synthetic 结论对线上流量的迁移性 |

---

## 面试追问建议回答（更新版）

- **为什么 dual 会在高阈值突然掉到 0？**  
  因为当前实现按“是否满足阈值”硬筛通过样本，超过阈值后无可行点时推荐值归零；这体现了风险偏好而非模型故障。

- **为什么 high_reuse 的拐点更靠右？**  
  因为其命中率分布整体更高，在更严格阈值下仍有样本可通过。

- **为什么 `hidden_reuse_ready_perc` 目前不敏感？**  
  当前没有默认启用 block 语义补丁，缺少可复用块真值信号。
