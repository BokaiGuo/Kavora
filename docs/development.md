# 开发环境

## 已验证工具链

当前项目开发环境已验证以下版本：

| 工具 | 版本 |
|---|---|
| Go | `go1.26.5 linux/amd64` |
| Rust | `rustc 1.95.0` |
| Cargo | `cargo 1.95.0` |
| Protocol Buffers compiler | `libprotoc 3.21.12` |
| `protoc-gen-go` | `v1.36.11` |
| `protoc-gen-go-grpc` | `v1.6.2` |
| `protoc-gen-prost` | `v0.5.0` |
| `protoc-gen-tonic` | `v0.5.0` |
| Python | `>=3.10` |

运行环境门禁：

```bash
bash scripts/check_dev_env.sh
```

脚本会在工具缺失时失败；Go 版本与已验证版本不同时会给出警告，便于后续评估兼容性，而不会无理由阻止较新的补丁版本。

## 本机 Go 安装位置

Go 使用官方 `go1.26.5.linux-amd64.tar.gz` 安装，并在解压前通过 Go 官方下载 API 返回的 SHA-256 校验值验证：

```text
~/.local/opt/go1.26.5
~/.local/opt/go -> ~/.local/opt/go1.26.5
~/.local/bin/go -> ~/.local/opt/go/bin/go
~/.local/bin/gofmt -> ~/.local/opt/go/bin/gofmt
```

`~/.local/bin` 已位于当前 `PATH`，因此不需要设置全局 `GOROOT`。Go workspace 和依赖缓存继续使用 `go env` 的用户级默认位置。

## 安装 protobuf Go 插件

为了让协议生成可重复，项目记录已验证版本。新环境可执行：

```bash
GOBIN="$HOME/.local/bin" go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11
GOBIN="$HOME/.local/bin" go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.2
cargo install --locked protoc-gen-prost --version 0.5.0
cargo install --locked protoc-gen-tonic --version 0.5.0
```

后续 `scripts/generate_proto.sh` 必须依赖环境门禁，并在生成代码中保持明确的协议版本边界。

## 工作区布局

| 路径 | 职责 |
|---|---|
| `gateway/` | Go 网关、后端客户端、路由和控制面代码 |
| `policy-engine/` | Rust 策略引擎及其测试 |
| `proto/` | 阶段一 Task 2 开始维护的跨语言 protobuf 契约 |
| `exporter/`、`benchmark/`、`planner/` | 现有 Python 指标、实验和离线规划链路 |
| `scripts/` | 环境检查、协议生成和本地运行入口 |

Go 使用仓库根目录的 `go.mod`，Rust 当前使用 `policy-engine/Cargo.toml`。在引入第二个 Rust crate 前不创建多余的 Cargo workspace。

## 生成文件策略

- `build/` 和 `policy-engine/target/` 是本地构建产物，不进入版本控制；
- `policy-engine/Cargo.lock` 作为可执行程序的依赖锁文件进入版本控制；
- Task 2 生成的 protobuf Go/Rust 源码将进入版本控制，CI 通过重新生成后执行 `git diff --exit-code` 检查一致性；
- 不直接编辑生成的 protobuf 源码，协议变更只能从 `proto/` 下的 `.proto` 文件发起。

统一入口：

```bash
make check-env
make test-go test-rust test-python
make build
```

## Fake inference backend

阶段一的网关测试默认使用确定性的 OpenAI-compatible fake backend，不依赖 GPU：

```bash
go run ./gateway/cmd/fake-backend \
  -listen 127.0.0.1:18080 \
  -ttft 20ms \
  -chunk-interval 10ms \
  -chunk 'hello' \
  -chunk ' world'
```

发送流式请求：

```bash
curl --noproxy '*' -N http://127.0.0.1:18080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

使用 `-fail-after-chunks 1` 可生成缺少 `[DONE]` 的截断 SSE 流，用于验证网关的流中故障语义。

## Rust Policy Engine

启动 unary Policy Engine：

```bash
cargo run --manifest-path policy-engine/Cargo.toml
```

默认 socket 位于 `$XDG_RUNTIME_DIR/kavora/policy.sock`；若未设置 `XDG_RUNTIME_DIR`，则使用 `$HOME/.local/run/kavora/policy.sock`。运行目录权限设为 `0700`，socket 权限设为 `0600`。

可通过环境变量覆盖：

```bash
KAVORA_POLICY_SOCKET=/tmp/kavora-dev/policy.sock \
KAVORA_POLICY_BLOCKED_TERMS='blocked topic,internal secret' \
cargo run --manifest-path policy-engine/Cargo.toml
```

Policy Engine 当前宣告 request policy、stream policy、incremental JSON、Token budget 和 cache key 能力。

## CLI 与 GUI 入口

Kavora 提供两个面向不同使用方式的入口：

- `build/kavora`：脚本、终端和 Codex 使用的 CLI；默认输出人类可读文本，`--json` 输出稳定机器可读 JSON；
- `build/kavora-gateway`：服务入口，同时嵌入 `/ui/` 控制台和 `/healthz` 健康检查，不需要 Node 或额外静态服务器。

构建并查看帮助：

```bash
make build
./build/kavora --help
```

CLI 的认证优先级是 `--api-key`、`KAVORA_API_KEY`、`~/.config/kavora/config.json`。推荐用配置初始化，避免把密钥留在 shell history：

```bash
./build/kavora config init --api-key replace-with-a-random-development-key
./build/kavora doctor
./build/kavora --json doctor
./build/kavora chat --model demo-model --message 'summarize the gateway boundary'
./build/kavora chat --stream=false --message 'return one sentence'
```

浏览器访问 `http://127.0.0.1:18000/ui/` 可打开 Kavora Control Room。GUI 会复用同一个 `/v1/chat/completions` SSE 接口，展示请求状态、策略结果、request ID 和延迟。请在 session-only 输入框中填写租户 API Key；它只作为请求头发送，不写入页面或服务端存储。

## Unary Gateway 本地链路

分别启动 Rust Policy、Fake Backend 和 Go Gateway：

```bash
cp gateway/config.example.yaml gateway/config.yaml
cargo run --manifest-path policy-engine/Cargo.toml
go run ./gateway/cmd/fake-backend -listen 127.0.0.1:18080 -chunk 'hello from Kavora'
go run ./gateway/cmd/gateway
```

先把示例中的 API Key 替换为本地随机值。`gateway/config.yaml` 被 Git 忽略，不能把真实密钥提交到仓库。Gateway 默认监听 `127.0.0.1:18000`，并连接默认 Policy socket 与 `http://127.0.0.1:18080`。可通过以下变量覆盖：

- `KAVORA_GATEWAY_LISTEN`
- `KAVORA_BACKEND_URL`
- `KAVORA_POLICY_SOCKET`
- `KAVORA_TENANT_CONFIG`，默认 `gateway/config.yaml`
- `KAVORA_BACKEND_HEALTH_INTERVAL`：后端健康检查周期，默认 `15s`

请求必须携带由静态配置映射的 Bearer API Key：

```bash
curl --noproxy '*' http://127.0.0.1:18000/v1/chat/completions \
  -H 'authorization: Bearer replace-with-a-random-development-key' \
  -H 'content-type: application/json' \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}]}'
```

客户端不能提交或覆盖 tenant ID。Gateway 仅从 API Key 的 SHA-256 摘要映射服务端租户配置，并把该租户的 `token_budget`、`policy_fail_mode` 和请求 deadline 传给 Rust。`max_concurrent` 的槽位覆盖完整请求生命周期，包括后端流式响应。

`policy_fail_mode: closed` 在 Rust RPC 不可用或返回 retryable failure 时拒绝请求；`open` 会绕过不可用的策略检查继续转发。明确的 PII、内容或预算拒绝始终生效，不会被 fail-open 绕过。流已开始后的 fail-open 可能释放尚未检查的缓冲并继续直通，这是可用性优先租户的显式安全取舍。

后端可以和租户配置写在同一个 YAML 文件的 `backends` 节点中：

```yaml
backends:
  - id: vllm-local
    url: http://127.0.0.1:8000
    enabled: true
    weight: 2
    models: [qwen]
    health_path: /healthz
  - id: sglang-local
    url: http://127.0.0.1:8001
    enabled: true
    weight: 1
    models: [qwen]
```

Gateway 按模型过滤健康后端并执行加权轮询。HTTP 建连失败、非 2xx 响应会在首字节前切换候选；一旦后端成功返回并开始流式路径，后续故障只终止当前响应，不会重新生成请求。周期健康检查会把恢复的后端重新加入候选集。

## Readiness、Metrics 与审计

Gateway 暴露三个运维端点：

- `/healthz`：进程存活，不代表依赖已就绪；
- `/readyz`：Policy Engine 已在启动阶段连接成功，且至少存在一个启用且健康的后端时返回 `200`，否则返回 `503`；
- `/metrics`：Prometheus 文本格式，包含请求计数/延迟、策略决策、后端尝试和当前 in-flight 请求。

Go Gateway 会将 `request_id` 作为响应头、Policy protobuf context、后端 `X-Request-ID` 和 JSON 审计事件的关联键。Rust Policy Engine 对 unary policy 和 stream open 输出同一关联字段。审计只记录事件、租户、策略结果、后端、状态和耗时，不记录完整 prompt、消息、API Key 或 PII。

后端健康检查周期可用 `KAVORA_BACKEND_HEALTH_INTERVAL` 调整；审计事件默认输出到 Gateway stderr，便于被 systemd、容器 runtime 或日志采集器接收。

真实后端冒烟入口见 `docs/quickstart_gateway.md`。CI 默认继续使用 fake backend；没有 GPU、模型或对应服务时，`make smoke-vllm` / `make smoke-sglang` 会输出显式 `SKIP`，只有设置 `KAVORA_SMOKE_REQUIRED=true` 才会把环境缺失升级为失败。

运行真实 Go↔Rust 端到端测试：

```bash
make test-e2e-unary
```

Task 6 已支持透明 SSE 转发。Gateway 使用固定 32 KiB 缓冲同步读写；慢客户端写入阻塞时不会继续读取后端，从而避免应用层无界积压。流开始后的后端截断会原样表现为缺少 `[DONE]` 的结束流，不会透明重试或伪造完成事件。

跨语言流式门禁可运行：

```bash
make test-e2e-stream
```

Task 7 已加入 Rust 增量响应策略：

- Go 按固定 chunk 将原始 SSE 字节送入 Rust；
- Rust 只在完整 SSE event、有效 JSON、PII/内容规则和剩余 Token 预算全部通过后返回 `release_bytes`；
- Go 只释放 Rust 明确批准的缓冲前缀；
- 未释放缓冲默认上限为 64 KiB，策略检查受 Gateway request deadline 约束；
- `[DONE]` 缺失、Malformed JSON、超限或明确的 PII/内容拒绝都会终止流；策略 RPC 故障依据租户 fail mode 处理。

可调参数：

- `KAVORA_REQUEST_TIMEOUT`：请求和策略流总 deadline，默认 `60s`；
- `KAVORA_STREAM_CHUNK_BYTES`：Go→Rust 检查批次，默认 `16384`；
- `KAVORA_STREAM_BUFFER_BYTES`：尚未获准释放的最大缓冲，默认 `65536`。

当前 PII/内容扫描保证覆盖同一完整 SSE event，即使该 event 被拆分为多个 transport chunk。跨多个已经独立完成的 SSE event 才拼接形成的敏感字符串暂不作为已解决能力。
