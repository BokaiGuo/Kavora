# Kavora

[English](README.md) | [简体中文](README.zh-CN.md)

> **Route intelligently. Govern safely. Remember efficiently.**

![Status](https://img.shields.io/badge/status-alpha-orange)
![Go](https://img.shields.io/badge/Go-gateway-00ADD8?logo=go&logoColor=white)
![Rust](https://img.shields.io/badge/Rust-policy%20%26%20runtime-000000?logo=rust&logoColor=white)
![Python](https://img.shields.io/badge/Python-observability%20%26%20research-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-66%20passing-2ea44f)

Kavora is an open-source **AI inference control plane** for local and private LLM serving.

It combines three deliberately separated layers:

- **Go** — OpenAI-compatible Gateway, tenant controls, routing, streaming, CLI, and GUI.
- **Rust** — PII/content policy, incremental JSON/SSE inspection, token budgets, cache keys, and Wasmtime execution.
- **Python** — vLLM/SGLang metric normalization, backend state, tuning advice, reproducible experiments, and reports.

The name **Kavora** combines **KV**—KV cache, prefix reuse, and inference runtime state—with **Agora**—a shared control space where requests, models, policies, metrics, and tools are governed.

## Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Quick Start](#quick-start)
- [Operations](#operations)
- [Research Workflow](#research-workflow)
- [Development](#development)
- [Project Status](#project-status)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Security](#security)

## Overview

Kavora is designed to be useful in three contexts:

| Context | Outcome |
|---|---|
| **Engineering showcase** | A visible Go/Rust boundary, GUI/CLI, streaming policy enforcement, and failure demonstrations. |
| **Local operations** | Continuous monitoring, quality-aware state, tuning advice, health checks, failover, and safe routing. |
| **Research** | Reproducible workloads, strategy comparisons, replay artifacts, promotion gates, and paper-style reports. |

See [`docs/three_goals.md`](docs/three_goals.md) for the product acceptance criteria. The project is currently **alpha**: the end-to-end control plane is implemented and tested, while performance claims remain evidence-bound.

## Architecture

```mermaid
flowchart LR
    U[CLI / GUI / OpenAI Client] --> G[Go Gateway]
    G --> T[Tenant Auth & Rate Limit]
    G --> P[Policy RPC]
    P --> R[Rust Policy Engine]
    G --> Q[Static / Shadow / Enforced Router]
    Q --> V[vLLM]
    Q --> S[SGLang]
    V --> O[Python Observer]
    S --> O
    O --> B[Versioned backend-state]
    B --> Q
    O --> A[Tuning Advice]
    O --> E[Experiment Artifacts]
    E --> X[JSON / Markdown Research Report]
```

### Request path

1. Client sends an OpenAI-compatible request to the Go Gateway.
2. Go authenticates the tenant, applies limits and assigns a request ID.
3. Rust evaluates JSON, PII, content, token budget and cache-key policy.
4. Router selects a healthy backend using static, shadow or enforced mode.
5. Gateway preserves unary/SSE semantics and records metrics/audit events.
6. Python Observer turns backend metrics into quality-aware state and tuning advice.

## Features

- OpenAI-compatible unary and SSE chat completions.
- Tenant authentication, concurrency limits, token budgets, and policy fail modes.
- Rust policy evaluation over Unix Socket or gRPC.
- Incremental JSON and SSE inspection with bounded streaming buffers.
- PII and content filtering before backend dispatch and during streaming responses.
- Backend health checks, model matching, weighted candidates, and failover.
- Static, shadow, and enforced routing with stale/missing state fallback.
- Tenant-scoped prefix affinity with TTL and capacity bounds.
- Prometheus metrics, JSON audit events, request IDs, and an embedded GUI.
- Versioned backend-state snapshots and persistent tuning advice.
- Digest-verified Wasmtime execution with resource and capability controls.
- Reproducible benchmarks, replay artifacts, promotion gates, and Markdown/JSON reports.

## Quick Start

### One-command showcase

The showcase starts a deterministic fake backend, Rust Policy Engine and Go Gateway, then demonstrates CLI unary chat, SSE streaming, backend status and PII rejection:

```bash
make demo-kavora
```

The command generates:

```text
results/demo/showcase.json
```

It prints the temporary GUI URL. The normal GUI entry is:

```text
http://127.0.0.1:18000/ui/
```

### CLI

```bash
build/kavora doctor
build/kavora backends
build/kavora chat --message "Explain the Kavora request path"
build/kavora advice
```

### Build everything

```bash
make build
```

## Run With vLLM or SGLang

See [`docs/quickstart_gateway.md`](docs/quickstart_gateway.md) for complete setup.

Typical Gateway configuration uses:

```yaml
backends:
  - id: local-vllm
    url: http://127.0.0.1:8000
    models: [kvcache-local-tiny]
    health_path: /health
```

Run a real backend smoke test:

```bash
KAVORA_API_KEY=replace-with-your-key \
KAVORA_SMOKE_MODEL=kvcache-local-tiny \
KAVORA_SMOKE_REQUIRED=true \
make smoke-vllm
```

The same flow is available for SGLang with `make smoke-sglang`.

## Long-Running Monitoring

Start the Observer alongside the local inference server:

```bash
KVCACHE_BACKEND_METRICS_URL=http://127.0.0.1:8000/metrics \
KVCACHE_MODEL_NAME=kvcache-local-tiny \
KVCACHE_STATE_DIR=results/kavora-state \
python3 -m exporter.app
```

Live endpoints:

```bash
curl http://127.0.0.1:9108/backend-state
curl http://127.0.0.1:9108/advice
```

Persistent outputs:

```text
results/kavora-state/backend-state.json
results/kavora-state/advice.jsonl
```

The Observer preserves `fresh`, `stale`, `missing` and `invalid` quality. Missing metrics are never silently converted into zero.

## Research Experiments

### Stage 1 Gateway benchmark

```bash
make benchmark-stage1
```

### Stage 2 KV-aware matrix

Create a real-endpoint config from the checked-in template:

```bash
cp benchmark/config.stage2.template.yaml benchmark/config.stage2.yaml
# Edit model/revision, backend version, five target endpoints, and two metrics endpoints.
make benchmark-stage2-config
```

Run the complete matrix:

```bash
make benchmark-stage2
```

The Stage 2 command intentionally refuses to invent proxy performance rows. It requires `direct`, `static`, `load-aware`, `kv-aware-shadow`, and `kv-aware-enforced` endpoints, at least two backend metrics endpoints, four controlled workloads, and at least ten repetitions. See [`docs/stage2_results.md`](docs/stage2_results.md).

For a local model that fits as two replicas on the available GPU, run the complete stack and matrix with one command:

```bash
MODEL=/absolute/path/to/local-model make stage2-local
```

This starts two vLLM replicas, two Observers, one Rust Policy Engine, and four independently configured Gateway processes. It polls live queue/KV state during the run and tears the stack down afterward.

### Paper-style report

```bash
make research-report
```

Generated artifacts:

```text
results/research/research_report.json
results/research/research_report.md
results/research/reproduction_manifest.json
```

The report records Git revision, hardware, Python version, seed, config hashes, baselines, latency/throughput metrics and limitations. It separates real measurements, proxy measurements, smoke evidence and blocked evidence.

## Go/Rust Boundary

| Component | Language | Responsibility | Boundary |
|---|---|---|---|
| `kavora-gateway` | Go | Gateway, tenants, routing, streaming, observability | HTTP/OpenAI API |
| `kavora-policy` | Rust | Policy, JSON/SSE checks, budgets, cache keys | protobuf over Unix Socket/gRPC |
| `kavora-tool-worker` | Rust | Digest-verified Wasmtime execution | JSONL process boundary |
| `exporter` | Python | Metrics normalization, state, advice | Prometheus + JSON state |
| `benchmark` | Python | Reproducible workload and reports | JSON/Markdown artifacts |

This is not an artificial mixed-language demo: each language owns a real engineering boundary.

## Repository Layout

```text
gateway/          Go Gateway, CLI, backend registry, router, GUI
policy-engine/    Rust Policy Engine and Wasmtime worker
proto/            Versioned protobuf contracts and golden fixtures
exporter/         vLLM/SGLang metrics, state and advice
benchmark/        Gateway/KV-aware experiments and research reports
planner/          Capacity and recommendation logic
scripts/          Build, demo, smoke, gate and report entrypoints
docs/             Architecture, quickstarts, methods and limitations
tests/            Python contract, benchmark and quality tests
```

## Verification

The current project gate includes:

```bash
python3 -m pytest -q
go test -race ./gateway/...
go vet ./...
cargo test --manifest-path policy-engine/Cargo.toml
cargo clippy --manifest-path policy-engine/Cargo.toml --all-targets -- -D warnings
bash scripts/generate_proto.sh --check
make build
```

The latest local validation passed with **66 Python tests**, Go race/vet, Rust test/clippy, protobuf consistency and full builds.

## Documentation

- [`docs/three_goals.md`](docs/three_goals.md) — three product goals and acceptance criteria
- [`docs/project_overview.md`](docs/project_overview.md) — architecture and scope
- [`docs/quickstart_gateway.md`](docs/quickstart_gateway.md) — Gateway, vLLM/SGLang and monitoring setup
- [`docs/staged_goals.md`](docs/staged_goals.md) — staged implementation goals
- [`docs/stage1_benchmark.md`](docs/stage1_benchmark.md) — Stage 1 benchmark protocol
- [`docs/stage2_results.md`](docs/stage2_results.md) — KV-aware routing experiment
- [`docs/tool_manifest.md`](docs/tool_manifest.md) — secure tool contract
- [`docs/project_status.md`](docs/project_status.md) — delivered capabilities and claim boundaries

## Status

Kavora is an actively developed alpha. The end-to-end control plane, monitoring/advice loop, showcase demo and reproducible research report pipeline are implemented. Performance claims remain evidence-bound: the system reports what was measured and does not turn a smoke test into a throughput claim.

## Contributing

Contributions are welcome. Please keep changes focused, preserve the Go/Rust protocol contracts, add tests for behavior changes, and update the relevant benchmark or documentation artifact when changing observable behavior.

Before opening a pull request, run:

```bash
python3 -m pytest -q
go test -race ./gateway/...
cargo test --manifest-path policy-engine/Cargo.toml
```

## Security

Do not include real API keys, private model paths, customer prompts, or production traces in issues or pull requests. Review [`docs/tool_manifest.md`](docs/tool_manifest.md) and [`docs/project_status.md`](docs/project_status.md) before enabling experimental tool execution or enforced routing.
