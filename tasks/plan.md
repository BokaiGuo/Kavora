# Implementation Plan: Kavora

## Overview

本计划将现有 KV cache 可观测与离线实验仓库逐步升级为 Go LLM Gateway、Rust Policy Engine 和 Python KV 观测/实验层协同的 AI Infra 项目。实施遵循三个阶段：先交付可展示的双语言智能网关，再接入可回退的 KV-aware 在线路由，最后加入可重放的安全 Agent Runtime。每个阶段都保留可运行闭环和明确的推广门禁。

设计依据：`docs/project_overview.md`、`docs/staged_goals.md` 和 `docs/superpowers/specs/2026-07-31-kvcache-control-plane-design.md`。

## Architecture Decisions

- Go 拥有 HTTP/SSE 请求生命周期、租户状态和后端路由。
- Rust 拥有确定性策略、字节流解析、Token 预算和 cache key。
- Go/Rust 采用 protobuf + gRPC；单机默认使用 Unix Domain Socket。
- Python exporter/benchmark/planner 保持独立，先通过稳定契约接入，不提前重写。
- 阶段一先用 fake backend 打通完整链路，再验证真实 vLLM/SGLang。
- KV-aware 路由必须依次经过 `shadow -> advisory -> enforced` 门禁。
- Agent Runtime 只在前两个阶段稳定后加入，并复用既有身份、策略、预算和 trace。

## Current Prerequisites

- Rust `1.95.0` 与 Cargo 已安装。
- `protoc 3.21.12` 已安装。
- 当前环境未检测到 Go 工具链，Task 0 是硬门禁。
- `buf` 当前未安装；首版不依赖 buf，使用固定 `protoc` 命令生成代码。
- 工作区已有大量未提交改动，所有实现必须只改任务声明的文件，并禁止清理或覆盖既有改动。

## Dependency Graph

```text
Task 0 toolchain
  -> Task 1 repository skeleton
      -> Task 2 protocol contract
          -> Task 3 fake backend
          -> Task 4 Rust unary policy
              -> Task 5 Go unary gateway slice
                  -> Task 6 streaming proxy
                      -> Task 7 streaming policy/backpressure
                          -> Task 8 tenants/limits
                              -> Task 9 backend failover
                                  -> Task 10 observability
                                      -> Task 11 real backends/demo
                                          -> Task 12 performance/fault report
                                              -> Stage 1 gate
                                                  -> Tasks 13-17 KV-aware routing
                                                      -> Stage 2 gate
                                                          -> Tasks 18-21 Agent runtime
```

## Stage 1: Bilingual LLM Gateway

### Task 0: Establish the Go toolchain gate

**Description:** 安装或定位受支持的 Go 工具链，并记录 Go、Rust、protoc 和生成插件版本，使后续构建可重复。

**Acceptance criteria:**
- [ ] `go version` 可用，版本策略写入开发文档。
- [ ] `protoc-gen-go` 与 `protoc-gen-go-grpc` 可用。
- [ ] 环境检查脚本能对缺失依赖给出明确错误。

**Verification:**
- [ ] Run: `bash scripts/check_dev_env.sh`
- [ ] Run: `go version && rustc --version && protoc --version`

**Dependencies:** None

**Files likely touched:**
- `scripts/check_dev_env.sh`
- `docs/development.md`

**Estimated scope:** Small

### Task 1: Create the polyglot workspace skeleton

**Description:** 建立 Go gateway、Rust policy engine、protobuf 和统一构建入口，不加入业务功能，确保三种语言的现有与新增测试彼此隔离。

**Acceptance criteria:**
- [ ] Go 与 Rust 最小程序分别构建并通过测试。
- [ ] 根级命令可执行 Python、Go、Rust 的定向检查。
- [ ] 目录职责和生成文件策略有文档说明。

**Verification:**
- [ ] Run: `make test-go test-rust test-python`
- [ ] Run: `make build`

**Dependencies:** Task 0

**Files likely touched:**
- `Makefile`
- `go.mod`
- `gateway/cmd/gateway/main.go`
- `policy-engine/Cargo.toml`
- `policy-engine/src/main.rs`

**Estimated scope:** Medium

### Task 2: Define the versioned policy contract

**Description:** 定义健康检查、请求策略判定、稳定错误码、trace/deadline 元数据和流式策略消息，并生成 Go/Rust 客户端与服务端代码。

**Acceptance criteria:**
- [ ] proto 覆盖 `EvaluateRequest`、能力发现和双向流式检查。
- [ ] 判定结果区分 allow、block、terminate、retryable failure。
- [ ] 生成过程可重复且 CI 能检测脏生成文件。

**Verification:**
- [ ] Run: `make proto && git diff --exit-code -- proto/gen`
- [ ] Contract tests decode the same golden messages in Go and Rust.

**Dependencies:** Task 1

**Files likely touched:**
- `proto/policy/v1/policy.proto`
- `scripts/generate_proto.sh`
- `gateway/internal/policycontract/contract_test.go`
- `policy-engine/tests/contract.rs`

**Estimated scope:** Medium

### Task 3: Build a deterministic fake inference backend

**Description:** 提供测试专用 OpenAI-compatible fake backend，可控制普通响应、SSE chunk、首字节延迟、中途失败和取消观察，避免早期依赖 GPU 后端。

**Acceptance criteria:**
- [ ] 支持普通与流式 chat completions。
- [ ] 测试可配置 TTFT、chunk 间隔和故障点。
- [ ] 能证明客户端取消已传播到 fake backend。

**Verification:**
- [ ] Run: `go test ./gateway/internal/fakebackend/...`
- [ ] Manual: curl receives deterministic SSE fixtures.

**Dependencies:** Task 2

**Files likely touched:**
- `gateway/internal/fakebackend/server.go`
- `gateway/internal/fakebackend/server_test.go`
- `gateway/cmd/fake-backend/main.go`

**Estimated scope:** Medium

### Task 4: Implement the Rust unary policy slice

**Description:** 实现首个可用策略服务，完成请求大小限制、基础 PII 规则、内容规则、Token 预算预检和规范化 cache key，并监听 UDS。

**Acceptance criteria:**
- [ ] 相同语义请求产生稳定 cache key。
- [ ] PII、内容规则和超限请求返回稳定策略码。
- [ ] 恶意大输入和深层 JSON 受资源上限约束。

**Verification:**
- [ ] Run: `cargo test --manifest-path policy-engine/Cargo.toml`
- [ ] Run: Rust integration test calls the service over UDS.

**Dependencies:** Task 2

**Files likely touched:**
- `policy-engine/src/service.rs`
- `policy-engine/src/policy.rs`
- `policy-engine/src/cache_key.rs`
- `policy-engine/src/config.rs`
- `policy-engine/tests/unary_policy.rs`

**Estimated scope:** Medium

### Task 5: Deliver the first end-to-end unary request

**Description:** Go 网关完成 OpenAI-compatible 非流式入口、请求验证、Rust 策略调用和 fake backend 转发，形成第一个纵向闭环。

**Acceptance criteria:**
- [ ] 合法请求经过 Go -> Rust -> fake backend 后返回兼容响应。
- [ ] Rust block 被映射为稳定 HTTP 错误和 request ID。
- [ ] deadline 与客户端取消同时传递给策略和后端。

**Verification:**
- [ ] Run: `go test ./gateway/internal/gateway/...`
- [ ] Run: `make test-e2e-unary`

**Dependencies:** Tasks 3, 4

**Files likely touched:**
- `gateway/internal/gateway/server.go`
- `gateway/internal/gateway/chat.go`
- `gateway/internal/policyclient/client.go`
- `gateway/internal/backend/client.go`
- `gateway/internal/gateway/chat_test.go`

**Estimated scope:** Medium

## Checkpoint A: Unary vertical slice

- [ ] Python existing tests remain green.
- [ ] Go and Rust builds are clean.
- [ ] One command demonstrates allow and block flows.
- [ ] Contract and error semantics receive human review.

### Task 6: Add OpenAI-compatible SSE proxying

**Description:** 在不启用响应策略的情况下实现规范的 SSE 转发、flush、断连检测和流开始后的错误处理，先隔离验证 Go 流式代理本身。

**Acceptance criteria:**
- [ ] SSE chunk 顺序与终止标记保持兼容。
- [ ] 慢客户端不会导致无界读取。
- [ ] 流开始后的后端失败不会触发透明重试。

**Verification:**
- [ ] Run: `go test ./gateway/internal/streaming/...`
- [ ] Run: `make test-e2e-stream`

**Dependencies:** Task 5

**Files likely touched:**
- `gateway/internal/streaming/proxy.go`
- `gateway/internal/streaming/proxy_test.go`
- `gateway/internal/gateway/chat.go`
- `gateway/internal/gateway/stream_test.go`

**Estimated scope:** Medium

### Task 7: Add Rust incremental stream policy and backpressure

**Description:** 将后端 chunk 通过有界队列批量送入 Rust 双向流，进行增量 JSON/tool-call 检查、响应扫描和 Token 预算终止。

**Acceptance criteria:**
- [ ] 只有 Rust 确认允许的 chunk 才发送给客户端。
- [ ] 队列容量、批量大小和策略超时可配置。
- [ ] block、timeout、queue-full 和 cancellation 有独立指标与测试。

**Verification:**
- [ ] Run: `cargo test --manifest-path policy-engine/Cargo.toml stream`
- [ ] Run: `go test ./gateway/internal/streaming/...`
- [ ] Run: bounded-memory slow-client integration test.

**Dependencies:** Task 6

**Files likely touched:**
- `policy-engine/src/stream.rs`
- `policy-engine/tests/stream_policy.rs`
- `gateway/internal/streaming/policy_pipe.go`
- `gateway/internal/streaming/policy_pipe_test.go`
- `gateway/internal/policyclient/client.go`

**Estimated scope:** Medium

### Task 8: Add tenant authentication and resource limits

**Description:** 引入静态配置驱动的 API Key、tenant policy、并发限制、请求预算以及 policy fail mode，不在首版引入数据库。

**Acceptance criteria:**
- [ ] API Key 只映射到服务端 tenant policy，不信任客户端 tenant ID。
- [ ] 并发限制和 Token 预算按租户隔离。
- [ ] Rust 不可用时按租户执行 fail-open 或 fail-closed。

**Verification:**
- [ ] Run: `go test ./gateway/internal/tenant/... ./gateway/internal/limits/...`
- [ ] Run: concurrent tenant isolation integration test.

**Dependencies:** Task 7

**Files likely touched:**
- `gateway/internal/tenant/config.go`
- `gateway/internal/tenant/auth.go`
- `gateway/internal/limits/limiter.go`
- `gateway/internal/gateway/middleware.go`
- `gateway/config.example.yaml`

**Estimated scope:** Medium

### Task 9: Add backend registry and safe failover

**Description:** 支持多个 vLLM/SGLang 兼容后端、周期健康检查和静态路由策略，仅允许首字节前故障转移。

**Acceptance criteria:**
- [ ] 后端可配置启停、权重、模型和健康检查。
- [ ] 非流式和流式首字节前故障可切换备用后端。
- [ ] 已开始流式输出后明确终止，不重复生成。

**Verification:**
- [ ] Run: `go test ./gateway/internal/backend/... ./gateway/internal/router/...`
- [ ] Run: failover integration tests with two fake backends.

**Dependencies:** Task 8

**Files likely touched:**
- `gateway/internal/backend/registry.go`
- `gateway/internal/backend/health.go`
- `gateway/internal/router/static.go`
- `gateway/internal/router/static_test.go`
- `gateway/config.example.yaml`

**Estimated scope:** Medium

## Checkpoint B: Production-shaped request path

- [ ] Unary and SSE paths both support policy, tenant limits and failover.
- [ ] Slow clients and component failures keep resources bounded.
- [ ] Full Go/Rust/Python targeted test suite passes.
- [ ] Demo uses only fake backends and is GPU-independent.

### Task 10: Wire metrics, tracing, readiness and audit

**Description:** 为 Go/Rust 请求路径增加统一 request ID、OpenTelemetry context、Prometheus 指标、组件 readiness 和脱敏审计记录。

**Acceptance criteria:**
- [ ] 一次请求可跨 Go、Rust 和后端关联 trace。
- [ ] readiness 区分必需组件和可降级组件。
- [ ] 日志与 audit 默认不包含完整 prompt、PII 或 API Key。

**Verification:**
- [ ] Run: telemetry unit and integration tests.
- [ ] Manual: inspect one allow trace and one block trace.

**Dependencies:** Task 9

**Files likely touched:**
- `gateway/internal/telemetry/telemetry.go`
- `gateway/internal/audit/writer.go`
- `gateway/internal/gateway/health.go`
- `policy-engine/src/telemetry.rs`
- `deploy/prometheus/prometheus.yml`

**Estimated scope:** Medium

### Task 11: Integrate real vLLM and SGLang backends

**Description:** 复用现有启动脚本和 exporter，把真实本地后端纳入网关冒烟流程，同时保留 fake backend 作为默认 CI 路径。

**Acceptance criteria:**
- [ ] vLLM 与 SGLang 各有独立网关配置和冒烟命令。
- [ ] 现有 exporter 指标语义与网关运行互不冲突。
- [ ] 无 GPU 环境能明确跳过，不将环境阻塞误报为功能失败。

**Verification:**
- [ ] Run: `make smoke-vllm` when available.
- [ ] Run: `make smoke-sglang` when available.
- [ ] Run existing Python contract tests.

**Dependencies:** Task 10

**Files likely touched:**
- `scripts/run_gateway_local.sh`
- `scripts/smoke_gateway_backend.sh`
- `gateway/config.vllm.example.yaml`
- `gateway/config.sglang.example.yaml`
- `docs/quickstart_gateway.md`

**Estimated scope:** Medium

### Task 12: Build the Stage 1 benchmark and failure demo

**Description:** 建立直连、Go-only、Go+Rust unary、Go+Rust streaming 四条路径的可重复性能对比，并生成故障注入与展示材料。

**Acceptance criteria:**
- [ ] 输出 p50/p95/p99、TTFT、吞吐、错误率和内存峰值。
- [ ] 分离 Go 转发、RPC 和 Rust 策略开销。
- [ ] 一条命令演示 allow、PII block、预算终止和后端切换。

**Verification:**
- [ ] Run: `make benchmark-stage1`
- [ ] Run: `make demo-stage1`
- [ ] Generated report includes environment and config hashes.

**Dependencies:** Task 11

**Files likely touched:**
- `benchmark/gateway_runner.py`
- `benchmark/gateway_report.py`
- `scripts/demo_stage1.sh`
- `docs/stage1_benchmark.md`
- `README.md`

**Estimated scope:** Medium

## Stage 1 Promotion Gate

- [ ] Fake-backend E2E tests are deterministic and green.
- [ ] At least one real backend completes smoke validation.
- [ ] Policy and streaming failure semantics are documented and tested.
- [ ] Gateway overhead is measured; no unsupported performance claim is made.
- [ ] Stage 1 demo and benchmark artifacts are reproducible.

## Stage 2: KV-aware Routing

### Task 13: Define the backend-state contract

**Description:** 将现有 exporter 的 KV、压力、延迟和质量语义投影为版本化 backend-state 快照，严格保留零值、缺失、陈旧和混合质量区别。

**Acceptance criteria:**
- [x] Python producer 与 Go consumer 共享 golden fixtures。
- [x] 每个信号包含时间戳、质量和来源。
- [x] 缺失值不能被默认解释为零。

**Verification:** Contract tests pass in Python and Go.

**Dependencies:** Stage 1 gate

**Files likely touched:** `proto/backend/v1/backend_state.proto`, `exporter/app.py`, `gateway/internal/backendstate/client.go`, `tests/test_contract_e2e.py`

**Estimated scope:** Medium

### Task 14: Implement shadow route evaluation

**Description:** 在不改变实际静态路由的前提下计算 KV-aware 建议，并记录候选、特征、得分和决策原因。

**Acceptance criteria:**
- [ ] shadow 决策不影响真实后端选择。
- [ ] 决策日志可按 request ID 查询。
- [ ] stale/missing 信号触发明确降级原因。

**Verification:** Golden route cases and E2E shadow tests pass.

**Dependencies:** Task 13

**Files likely touched:** `gateway/internal/router/kvaware.go`, `gateway/internal/router/explain.go`, `gateway/internal/router/kvaware_test.go`, `gateway/internal/audit/writer.go`

**Estimated scope:** Medium

### Task 15: Add prefix affinity and bounded state

**Description:** 使用 Rust 生成的 cache key 建立有 TTL 和容量上限的 prefix-to-backend affinity，避免无界增长和跨租户泄漏。

**Acceptance criteria:**
- [ ] affinity 按租户隔离并有 TTL/容量限制。
- [ ] 后端下线会失效关联状态。
- [ ] 命中、淘汰和冲突均有指标。

**Verification:** Deterministic eviction and isolation tests pass.

**Dependencies:** Task 14

**Files likely touched:** `gateway/internal/router/affinity.go`, `gateway/internal/router/affinity_test.go`, `gateway/internal/router/kvaware.go`

**Estimated scope:** Small

### Task 16: Add advisory and enforced safety controls

**Description:** 增加路由模式切换、迟滞、冷却、最大偏移和静态回退，使 KV-aware 策略能够安全晋级或立即撤回。

**Acceptance criteria:**
- [ ] 模式切换有审计记录且无需重启。
- [ ] 信号质量下降自动回退 static。
- [ ] enforced 受冷却和最大流量比例约束。

**Verification:** Mode-transition and rollback fault tests pass.

**Dependencies:** Task 15

**Files likely touched:** `gateway/internal/router/mode.go`, `gateway/internal/router/guardrails.go`, `gateway/internal/router/guardrails_test.go`, `gateway/internal/admin/routes.go`

**Estimated scope:** Medium

### Task 17: Run the KV-aware experiment matrix

**Description:** 比较 static、load-aware、KV-aware shadow 与 enforced，在重复 workload 下生成机制和边界报告。

**Acceptance criteria:**
- [ ] 报告包含吞吐、TTFT、尾延迟、错误率、KV 命中和切换次数。
- [ ] 实验记录版本、硬件、模型、配置与种子。
- [ ] 晋级 enforced 需要预先声明的判定门槛。

**Verification:** `make benchmark-stage2` generates validated machine-readable and Markdown reports.

**Dependencies:** Task 16

**Files likely touched:** `benchmark/kvaware_experiment.py`, `benchmark/kvaware_report.py`, `benchmark/config.kvaware.yaml`, `docs/stage2_results.md`

**Estimated scope:** Medium

## Stage 2 Promotion Gate

- [ ] Shadow decisions are explainable and replayable.
- [ ] Missing/stale metrics always fall back safely.
- [ ] Repeated experiments justify enforced mode, or a no-go boundary is recorded.
- [ ] Static routing remains available as immediate rollback.

## Stage 3: Secure Agent Runtime

### Task 18: Define the tool manifest and execution contract

**Description:** 定义工具身份、哈希、输入输出 schema、文件/网络/时间/内存权限和稳定执行状态。

**Acceptance criteria:** Manifest validates deterministically; incompatible tools are rejected before execution.

**Verification:** Cross-language golden contract tests pass.

**Dependencies:** Stage 2 gate

**Files likely touched:** `proto/tool/v1/tool.proto`, `docs/tool_manifest.md`, `gateway/internal/tools/manifest.go`, `policy-engine/tests/tool_contract.rs`

**Estimated scope:** Medium

### Task 19: Build the Rust Wasmtime worker

**Description:** 在独立 Rust worker 中执行签名/哈希校验后的 WASM 工具，并强制资源与能力限制。

**Acceptance criteria:** 文件越权、网络越权、超时和内存超限均被隔离且返回稳定状态。

**Verification:** Cargo sandbox and adversarial integration tests pass.

**Dependencies:** Task 18

**Files likely touched:** `tool-runtime/Cargo.toml`, `tool-runtime/src/runtime.rs`, `tool-runtime/src/limits.rs`, `tool-runtime/src/service.rs`, `tool-runtime/tests/sandbox.rs`

**Estimated scope:** Medium

### Task 20: Add the Go agent loop

**Description:** 实现单 Agent 的模型调用、工具调用、重试、取消和状态机，复用现有 tenant、policy、route 和 trace。

**Acceptance criteria:** 一个受控任务可端到端完成；取消和预算对模型与工具同时生效。

**Verification:** Fake-model and fake-tool deterministic E2E tests pass.

**Dependencies:** Task 19

**Files likely touched:** `gateway/internal/agent/loop.go`, `gateway/internal/agent/state.go`, `gateway/internal/agent/loop_test.go`, `gateway/internal/tools/client.go`

**Estimated scope:** Medium

### Task 21: Add deterministic execution replay

**Description:** 记录模型事件、工具输入输出摘要、策略版本、路由决策和外部响应，使执行无需再次调用模型即可重放。

**Acceptance criteria:** 重放能检测事件、策略或工具版本差异，敏感原文不默认落盘。

**Verification:** `make test-replay` reproduces a golden execution and detects a tampered tool artifact.

**Dependencies:** Task 20

**Files likely touched:** `gateway/internal/replay/record.go`, `gateway/internal/replay/replay.go`, `gateway/internal/replay/replay_test.go`, `docs/replay_format.md`

**Estimated scope:** Medium

## Final Checkpoint

- [ ] All Python, Go and Rust targeted suites pass.
- [ ] Stage 1 and Stage 2 benchmark artifacts remain reproducible.
- [ ] Agent tool sandbox survives adversarial tests.
- [ ] Docs distinguish implemented, smoke-tested, benchmarked and claim-ready evidence.
- [ ] Repository has one-command local demos for gateway, KV-aware routing and Agent execution.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Go toolchain unavailable | High | Treat Task 0 as a hard gate; do not scaffold unverifiable Go code |
| gRPC per-chunk overhead | High | Batch bounded chunks; benchmark before considering FFI |
| Streaming policy increases TTFT | High | Measure separately; tune batch/time windows; keep explicit policy modes |
| Backend API differences | Medium | Normalize behind backend clients and retain fake backend contract tests |
| KV metrics are missing or stale | High | Preserve quality semantics and fall back to static routing |
| Prefix affinity leaks tenant behavior | High | Tenant-scoped keys, TTL, capacity bounds and hashed audit fields |
| Existing dirty worktree conflicts | High | Restrict each task to declared files; never reset unrelated changes |
| Agent scope overwhelms core project | High | Stage 3 remains blocked until Stage 2 gate is reviewed |

## Open Questions Deferred to Task Design

- Stage 1 Go HTTP router choice: prefer standard `net/http` unless benchmarks or ergonomics justify Chi.
- Token estimation implementation: begin with explicit conservative estimator; model-specific tokenizer is a later capability.
- Tenant persistence: static configuration for Stage 1; database only when runtime mutation is required.
- Policy language: typed built-in rules first; no general-purpose DSL in Stage 1.
- Enforced routing threshold: must be derived from Stage 2 shadow evidence, not selected in advance.
