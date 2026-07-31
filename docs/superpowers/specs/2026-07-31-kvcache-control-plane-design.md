# Kavora 设计规格

**状态：** 待用户评审  
**日期：** 2026-07-31  
**实施阶段：** 阶段一设计，阶段二和阶段三保留接口边界

## 1. 背景

当前仓库已经具备 vLLM/SGLang 指标适配、KV cache 派生指标、自监控、压测、窗口指标、容量扫描与离线推荐能力。下一步不是把这些 Python 模块机械地改写成 Go 或 Rust，而是增加一个真正需要双语言协作的在线请求系统，并让现有观测证据逐步进入路由闭环。

## 2. 决策

项目采用“Go 控制面 + Rust 策略数据面 + Python 实验与指标验证层”的渐进式架构：

- Go 网关拥有客户端连接、租户状态、后端选择和请求生命周期；
- Rust Policy Engine 拥有确定性的字节流检查、结构解析、预算和 cache key；
- Python exporter/planner 在阶段一保持现状，阶段二通过稳定指标/状态契约向 Go 提供信号；
- Go/Rust 使用版本化 gRPC，单机默认监听 Unix Domain Socket；
- Agent Runtime 仅在阶段三加入，不进入阶段一关键路径。

## 3. 备选方案及取舍

### 方案 A：独立进程 + gRPC/UDS（采用）

优点是职责清晰、崩溃隔离、独立 profiling、协议可测试，并能自然演进为远程 worker。成本是序列化与跨进程切换，因此接口必须保持粗粒度并对流式 chunk 做批量处理。

### 方案 B：Rust 动态库 + cgo

调用延迟可能更低，但构建、交叉编译、内存所有权和崩溃隔离成本更高。它会让项目重点变成 FFI 工程，而不是 AI Infra 控制面。仅当基准证明 RPC 成为主要瓶颈时重新评估。

### 方案 C：Go/Rust 分别作为远程微服务

部署边界最清晰，但第一阶段引入 TCP、服务发现和网络故障会增加无关复杂度。gRPC 协议保留远程部署能力，但本地默认采用 UDS。

## 4. 阶段一组件

### 4.1 Go Gateway

- OpenAI-compatible `/v1/chat/completions`；
- 普通响应与 SSE 流式转发；
- API Key 到 tenant policy 的映射；
- 请求并发限制、Token 配额和 deadline；
- backend registry、健康检查、round-robin/least-loaded 路由；
- Rust Policy Engine client；
- request audit、Prometheus metrics 和 OpenTelemetry trace；
- 管理面的 readiness 与策略引擎状态。

### 4.2 Rust Policy Engine

- `EvaluateRequest`：PII、内容规则、结构验证、预算与 cache key；
- `OpenStream`/双向流：接收有界 chunk，返回 allow/block/terminate；
- 规则和策略版本加载；
- 可配置的扫描大小与执行时间上限；
- Prometheus metrics、trace propagation 和健康接口；
- 不保存租户主状态，重启后可由 Go 重新同步策略。

### 4.3 现有 Python 层

- exporter 继续抓取 vLLM/SGLang `/metrics`；
- benchmark 继续作为端到端和窗口指标基准；
- planner 暂不进入阶段一在线强制路由；
- 阶段二定义 backend-state contract 后再接入 Go。

## 5. 协议边界

核心消息至少包含：

- `request_id`、`tenant_id`、`trace_context`；
- policy version 与 fail mode；
- model、messages、tool schema 摘要和 generation 参数；
- deadline 与最大请求/响应字节数；
- token budget；
- 规范化 cache key 与策略判定结果；
- 稳定错误码，而不是依赖错误字符串。

协议演进遵循向后兼容字段添加规则。Go 网关在启动和运行期检查 Rust 服务的协议版本与能力集合。

## 6. 流式与背压

Go 从后端读取 SSE 后，不直接无限写入客户端或策略引擎。每个请求使用有界缓冲：

1. 后端 chunk 进入受限队列；
2. chunk 按大小或短时间窗口合并后交给 Rust；
3. Rust 返回允许、阻断或终止；
4. Go 仅转发已经允许的内容；
5. 慢客户端、Rust 超时或队列满触发明确的背压/取消策略。

该设计优先保证策略正确性。后续可增加“低风险租户的乐观转发”模式，但不能作为默认行为。

## 7. 失败语义

- Rust 拒绝：返回稳定策略码和可审计原因，不泄漏内部规则细节；
- Rust 超时/不可用：按租户配置 fail-open 或 fail-closed；
- 推理后端在首字节前失败：允许切换备用后端；
- 流式响应已经开始后失败：终止当前流，不透明重试生成；
- 客户端取消：同时取消后端请求和 Rust stream；
- 指标缺失：阶段二路由退回静态策略，不把缺失解释为零。

## 8. 安全边界

- 网关是租户身份和配额的权威来源；
- Rust 只接收完成策略判断所需的数据；
- UDS 使用最小文件权限；
- 日志默认不记录完整 prompt、PII 或密钥；
- audit 保存哈希、策略版本、结果和必要摘要；
- 所有输入都有字节数、深度、时间和并发限制。

## 9. 可观测性

端到端 trace 至少包含：认证、排队、请求策略、路由、后端 TTFT、流式策略和客户端写入。指标至少覆盖：

- 请求量、错误率、TTFT、总延迟和活跃流；
- 策略 allow/block/timeout、扫描字节和执行延迟；
- UDS/gRPC 错误、队列深度和背压事件；
- 后端健康、选择次数和故障转移；
- 阶段二的路由模式、建议/实际差异和信号质量。

## 10. 验证策略

- Go 与 Rust 各自进行单元测试；
- protobuf contract 使用跨语言契约测试；
- 使用 fake inference backend 验证 SSE、取消和故障；
- 使用真实 vLLM/SGLang 进行本地冒烟验证；
- benchmark 分层测量直连、Go-only、Go+Rust 和策略规则复杂度；
- 故障注入覆盖 Rust 重启、超时、慢客户端和后端中断。

## 11. 阶段边界

阶段一结束前，不实现 KV-aware enforced routing、WASM 工具执行和通用 Agent loop。阶段二只在 shadow 数据和重复实验支持后启用 enforced routing。阶段三复用已稳定的租户、策略、路由、预算和 trace，不重新建设另一套 Agent 控制面。

## 12. 待实施计划解决的问题

- 选择 Go HTTP router、配置格式和持久化最小方案；
- 定义首版 protobuf 字段与错误码；
- 确定仓库目录布局和构建入口；
- 确定 fake backend 与跨语言测试工具；
- 定义首个 benchmark 的硬件与负载基线。
