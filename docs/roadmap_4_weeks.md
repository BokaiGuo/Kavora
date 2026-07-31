# 未来 4 周研发 Roadmap

## 目标

把当前仓库推进到同时满足求职展示、真实可用和研究实验的 AI serving 控制平台 alpha 版。所有工作按三条主线组织：

1. **展示线**：稳定 CLI/GUI、真实后端 demo、架构说明和故障演示
2. **可用线**：backend-state、长期 exporter、safe routing、调优建议和持久化状态
3. **研究线**：`schema v3`、capacity sweep、策略矩阵、replay 和论文式报告

具体验收标准统一见 `docs/three_goals.md`。

---

## Week 1: Schema v3 收口

- 统一 run 级质量摘要字段：`cache_hit_ratio`、`metric_quality`、`hit_ratio_source`
- 在实验聚合层补 `quality_summary`
- 在 planner、阈值曲线 JSON 中补 `schema_version`
- 保持对 legacy snapshot-fallback 产物的兼容读取

验收标准：

- `summary.json` 的每个 run 都带 `quality`
- `aggregates.*.quality_summary` 可直接给报告和 planner 消费
- 现有测试继续通过

---

## Week 2: Capacity Sweep MVP

- 新增 `scripts/run_capacity_sweep.py`
- 支持按 `scenario x concurrency_values x repeats` 跑 sweep
- 输出 `summary.json` + `summary.md`
- 在每个 sweep point 输出 baseline / dual 推荐结果

验收标准：

- 本地能跑出 `results/capacity_sweeps/.../summary.json`
- 每个 point 同时含 run 明细、聚合摘要、推荐结果
- schema v3 字段在 sweep 产物中齐全

---

## Week 3: 方法学增强

- 在 sweep 结果里补 point ranking / feasible point 标注
- 支持 `num_requests`、`input_len`、`shared_prefix_ratio` 的扩展维度
- 输出更明确的“最高可行点 / 保守推荐点”
- 给 planner 增加最小可行点数和波动过滤

验收标准：

- sweep 结果不只是原始点集合，而是能回答“建议用哪个点”
- 报告里能区分“无可行点”和“数据质量不足”

---

## Week 4: 工程化和可运营性

- 增加图表/产物回归测试
- 把 capacity sweep 命令补进 `docs/quickstart_local.md`
- 输出一页式 markdown 报告模板
- 评估真实流量回放入口和 vLLM block patch 接入方案

验收标准：

- 新人能照文档跑出一次完整 sweep
- CI 至少能校验关键 JSON schema 与产物文件存在
- backlog 中的下一个 P1 有明确 owner 和入口

---

## 建议 Issue 拆解

1. `schema-v3-core`
   范围：run 级 `quality`、scenario 级 `quality_summary`、planner/curve `schema_version`

2. `capacity-sweep-mvp`
   范围：`scripts/run_capacity_sweep.py`、summary 产物、基础测试

3. `capacity-sweep-ranking`
   范围：feasible point 筛选、推荐点排序、报告字段设计

4. `docs-capacity-sweep`
   范围：quickstart、metric spec、benchmark protocol、troubleshooting 更新

5. `backend-semantic-alignment`
   范围：SGLang prefix/cache 语义校准，vLLM block 语义补丁接入

6. `ci-artifact-regression`
   范围：关键 JSON/CSV/PNG 产物存在性与字段完整性检查
