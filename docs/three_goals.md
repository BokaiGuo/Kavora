# Kavora 三条终极目标

Kavora 不以“把 Go 和 Rust 拼在一起”为终点，而以三个可以独立验收、互相增强的目标为终点。

## 1. 求职展示：让架构亮点可见

目标是让面试官在 10 分钟内看懂并看到：

- Go 负责 OpenAI-compatible Gateway、租户、限流、负载均衡、健康检查、CLI 和 GUI；
- Rust 负责策略引擎、增量 JSON/SSE 检查、Token 预算、cache key 与 Wasmtime 沙箱；
- Go/Rust 通过 protobuf + Unix Socket/gRPC 形成真实边界，而不是 FFI 拼接；
- GUI/CLI 能演示正常请求、流式输出、PII 拒绝、预算中断、后端故障转移和 enforced routing；
- 每个关键决策都有 request ID、审计事件、指标和可解释原因。

**展示验收：** 一条命令启动本地 demo；一条命令完成 smoke；GUI 能观察状态；README 能解释一次请求的完整路径。

## 2. 真实可用：成为本地推理服务控制面

目标是长期运行并服务本地 vLLM/SGLang：

- exporter 持续采集 KV、显存、延迟和质量信号；
- backend-state 保留 fresh、stale、missing、invalid，不把缺失误判为零；
- Gateway 在 static、shadow、enforced 之间安全切换；
- stale/missing 自动回退 static，路由 affinity 有租户隔离、TTL 和容量上限；
- CLI 提供 doctor、backends、chat、状态和诊断入口；
- 运行产物可保存，服务重启后仍能定位最近一次状态和异常。

**可用验收：** 连续运行、后端重启、指标缺失、策略服务异常时不丢请求语义、不泄漏租户状态，并产生可执行调优建议。

## 3. 研究实验：成为可复现的性能实验平台

目标是支持性能工程和论文式报告：

- static、load-aware、KV-aware shadow、KV-aware enforced 可比较；
- 实验固定版本、模型、硬件、配置、种子、请求规模和数据质量；
- 报告包含吞吐、TTFT、p95/p99、错误率、KV 命中、切换次数和资源使用；
- 结果同时输出机器可读 JSON 与 Markdown 报告；
- 支持 replay、golden fixture、阶段 promotion gate 和失败边界记录；
- 明确区分真实测量、proxy、smoke、replay 和环境阻塞证据。

**研究验收：** 新机器按文档可以复现实验；同一配置和种子得到相同决策语义；报告可以直接支撑方法、实验设置、结果和局限性章节。

## 三条线的关系

```text
真实本地服务
      │
      ▼
Observer ── backend-state ──► Gateway / Router ──► vLLM / SGLang
      │                              │
      ▼                              ▼
实验产物 / 报告 ◄────────────── CLI / GUI / 审计
```

求职展示要求架构和演示清晰；真实可用要求长期运行和安全回退；研究实验要求证据可复现。任何没有同时服务至少其中两条主线的复杂度，都不进入 Kavora 主路径。
