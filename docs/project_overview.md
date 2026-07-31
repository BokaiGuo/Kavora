# Kavora：项目概述

## 名称与品牌内涵

**Kavora** 源自 **KV + Agora**。

- **KV** 是项目的技术起点：KV cache、prefix reuse、缓存亲和性和推理运行时状态；
- **Agora** 原指公共汇聚空间，在这里代表请求、模型、策略、指标和工具共同进入的 AI 基础设施控制中心；
- 两者融合意味着 Kavora 不只是观测缓存，也负责让推理流量在统一规则下汇聚、判断、路由和执行。

Kavora 不是一个需要逐字展开的缩写，而是一个可扩展的产品品牌。它不会把项目限制在单一网关、单一模型后端或 KV cache 工具中，因此能够自然覆盖未来的 Policy Engine、Agent Runtime 和控制面生态。

推荐读音为 **kuh-VOR-uh**（`/kəˈvɔːrə/`），中文可称“卡沃拉”。

品牌标语：

> **Route intelligently. Govern safely. Remember efficiently.**  
> 智能路由，安全治理，高效记忆。

## 一句话定位

Kavora 是一个面向本地和私有化大模型推理集群的智能网关与执行控制平台：Go 负责可靠的请求控制、模型路由和流式传输，Rust 负责低延迟内容策略与安全执行，现有 KV Cache 可观测链路为在线路由和容量决策提供反馈。

## 三条终极目标

Kavora 的完成标准不是单纯“Gateway + Policy Engine 能启动”，而是同时满足：

| 目标 | 核心问题 | 代表性验收物 |
|---|---|---|
| 求职展示 | 能否在短时间展示架构和语言协同深度 | GUI、CLI、真实 vLLM smoke、跨语言调用、审计与故障演示 |
| 真实可用 | 能否长期监控并辅助调优本地推理服务 | exporter、backend-state、safe fallback、调优建议、持久化状态 |
| 研究实验 | 能否复现、比较并写成论文式结果 | seed/config/model/hardware manifest、replay、JSON/Markdown 报告、promotion gate |

这三条目标的详细验收标准见 `docs/three_goals.md`。

## 组件命名

| 组件 | 职责 |
|---|---|
| `kavora-gateway` | Go 实现的 OpenAI-compatible 网关、租户控制与模型路由 |
| `kavora-policy` | Rust 实现的内容策略、增量解析、预算和 cache key 引擎 |
| `kavora-observer` | KV cache 指标归一化、质量判定和后端状态输出 |
| `kavora-bench` | 可复现压测、策略对比与实验报告生成 |
| `kavora-agent` | 阶段三加入的 Go Agent 编排能力 |
| `kavora-runtime` | 阶段三加入的 Rust/Wasm 安全工具运行时 |
| `kavoractl` | 后续用于配置、诊断、重放和管理的命令行入口 |

## 项目要解决的问题

直接调用 vLLM 或 SGLang 只能完成推理请求，难以同时解决以下工程问题：

- 多模型、多后端和多租户请求如何统一接入；
- 如何在不破坏流式响应的前提下执行敏感信息、内容和预算策略；
- 如何把 KV cache 命中率、显存压力、队列和延迟用于路由，而不是只做离线看板；
- 如何解释一次请求为何被放行、拒绝、降级或路由到某个后端；
- 后续 Agent 工具执行如何复用同一套身份、策略、追踪和资源治理能力。

本项目将当前仓库从“KV cache 指标采集与离线实验工具”逐步演进为“推理流量控制面”，但不会在第一阶段同时实现完整 Agent Runtime。

## 为什么同时使用 Go 和 Rust

### Go：控制面与请求生命周期

Go 负责：

- OpenAI-compatible HTTP API；
- SSE 流式代理、取消传播和超时；
- 租户、API Key、配额和限流；
- 后端注册、健康检查、负载均衡和故障转移；
- 路由决策编排、审计记录和管理 API；
- 后续 Agent loop、工具调度和工作流状态管理。

这些职责以网络 I/O、并发协调和工程迭代为主，适合使用 goroutine、context 和成熟的 Go 云原生生态实现。

### Rust：低延迟策略数据面

Rust 负责：

- 请求和流式响应中的敏感数据扫描；
- JSON/tool-call 增量解析与结构约束；
- 内容策略匹配与确定性策略判定；
- Token 预算核算；
- prefix/cache key 的规范化与高速计算；
- 后续基于 Wasmtime 的受限工具执行。

这些职责位于高频数据路径，要求低尾延迟、内存安全、可预测资源占用以及对字节流的精细控制。

### 协作边界

Go 与 Rust 首选通过版本化 gRPC 协议通信。本地部署可使用 Unix Domain Socket 承载 gRPC，以兼顾清晰的进程隔离和较低通信成本。第一阶段不采用 `cgo`/FFI，避免把内存所有权、崩溃隔离和交叉编译复杂度带入网关主进程。

接口采用粗粒度设计：一次策略调用携带完整请求上下文，流式检查按有界 chunk 传递，并通过 request ID、deadline 和 cancellation 保持端到端一致性。

## 总体架构

```text
Client / Agent SDK
        |
        v
Go LLM Gateway
  |-- authentication / tenant / rate limit
  |-- OpenAI-compatible API and SSE streaming
  |-- backend registry and route orchestration
  |-- audit and trace context
  |
  +---- gRPC over UDS ----> Rust Policy Engine
  |                         |-- PII/content scanning
  |                         |-- incremental JSON parsing
  |                         |-- token budget
  |                         `-- cache-key calculation
  |
  +-----------------------> vLLM / SGLang backends
                                  |
                                  v
                         KV Cache Exporter
                                  |
                                  v
                       KV-aware Route Advisor
```

阶段二中，当前 Python exporter、benchmark 和 planner 继续承担指标语义验证与实验基准职责；稳定的在线决策接口再逐步接入 Go 网关。不会为了“语言统一”而提前重写已经验证过的实验链路。

## 核心请求流程

1. Go 网关认证租户并建立请求 deadline、trace 和预算上下文。
2. Go 调用 Rust Policy Engine，执行请求扫描、结构校验和 cache key 计算。
3. Go 根据静态配置、后端健康状态和租户约束选择候选后端。
4. 阶段二启用 KV-aware advisor，将 KV 命中、压力和延迟作为可解释的路由信号。
5. Go 将请求代理到 vLLM/SGLang，并把流式 chunk 以有界队列送往 Rust 做增量策略检查。
6. Go 向客户端转发允许的 SSE 事件；拒绝、超时和后端故障使用统一错误模型返回。
7. 系统记录策略版本、候选后端、路由原因、关键指标快照和最终状态。

## 项目展示重点

- 同一请求在 Go 与 Rust 间传播 trace、deadline、取消和结构化错误；
- Rust Policy Engine 故障时支持 fail-open/fail-closed 的租户级策略；
- 流式输出有界缓冲和背压，不无限积压内存；
- KV-aware 路由先 shadow、再 advisory、最后 enforced，避免未经验证直接控制生产流量；
- benchmark 对比直连、Go-only 网关、Go+Rust 策略、KV-aware 路由四种路径；
- 演示敏感数据拒绝、预算中断、后端故障转移和高复用 prompt 路由。

## 明确不做

- 第一阶段不实现通用 Agent 平台或多 Agent 协作；
- 第一阶段不引入 Kubernetes、Kafka、服务网格和复杂前端；
- 不在缺少实验证据时宣称 KV-aware 路由一定提升吞吐；
- 不用 FFI 制造“混合语言”亮点，语言边界必须对应真实工程职责；
- 不把现有 Python 实验代码一次性重写为 Go/Rust。

## 最终形态

完成三个阶段后，项目同时具备：可演示的双语言架构、可日常使用的本地推理网关、可复现的性能实验，以及可扩展为安全 Agent 执行平台的控制面基础。
