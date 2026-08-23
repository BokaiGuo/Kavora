.PHONY: all build build-fake-backend build-go build-cli build-rust check-env fmt proto proto-check test test-e2e-stream test-e2e-unary test-go test-python test-rust check-capacity-sweep-artifact smoke-vllm smoke-sglang benchmark-stage1 benchmark-stage2 benchmark-stage2-config benchmark-fidelity auto-calibrate fit-predictor validate-predictor policy-evaluation vllm-kv-events vllm-hash-resolver stage2-local demo-stage1 demo-kavora stage1-gate research-report cli-ui

GO ?= go
CARGO ?= cargo
PYTHON ?= python3

all: test build

check-env:
	bash scripts/check_dev_env.sh

build: build-go build-cli build-fake-backend build-rust

build-go:
	mkdir -p build
	$(GO) build -o build/kavora-gateway ./gateway/cmd/gateway

build-cli:
	mkdir -p build
	$(GO) build -o build/kavora ./gateway/cmd/kavora

cli-ui: build-cli
	./build/kavora ui

build-fake-backend:
	mkdir -p build
	$(GO) build -o build/kavora-fake-backend ./gateway/cmd/fake-backend

build-rust:
	$(CARGO) build --manifest-path policy-engine/Cargo.toml

fmt:
	$(GO) fmt ./...
	$(CARGO) fmt --manifest-path policy-engine/Cargo.toml -- --check

proto:
	bash scripts/generate_proto.sh

proto-check:
	bash scripts/generate_proto.sh --check

test: proto-check test-go test-rust test-python

test-go:
	$(GO) test ./...

test-e2e-unary: build-rust
	KAVORA_POLICY_BINARY=$(CURDIR)/policy-engine/target/debug/kavora-policy \
		$(GO) test -tags=integration ./gateway/internal/gateway -run TestGoGatewayUsesRustPolicyOverUDS -count=1

test-e2e-stream: test-e2e-unary

test-rust:
	$(CARGO) test --manifest-path policy-engine/Cargo.toml

test-python:
	$(PYTHON) -m pytest -q

check-capacity-sweep-artifact:
	@test -n "$(INPUT)" || (echo "usage: make check-capacity-sweep-artifact INPUT=results/capacity_sweeps/..."; exit 1)
	$(PYTHON) scripts/check_capacity_sweep_artifact.py "$(INPUT)"

smoke-vllm:
	BACKEND_KIND=vLLM KAVORA_TENANT_CONFIG=$${KAVORA_TENANT_CONFIG:-$(CURDIR)/gateway/config.vllm.example.yaml} bash scripts/smoke_gateway_backend.sh

smoke-sglang:
	BACKEND_KIND=SGLang KAVORA_TENANT_CONFIG=$${KAVORA_TENANT_CONFIG:-$(CURDIR)/gateway/config.sglang.example.yaml} bash scripts/smoke_gateway_backend.sh

benchmark-stage1:
	bash scripts/benchmark_stage1.sh

benchmark-stage2:
	bash scripts/benchmark_stage2.sh

benchmark-stage2-config:
	KAVORA_STAGE2_VALIDATE_ONLY=true bash scripts/benchmark_stage2.sh

benchmark-fidelity:
	$(PYTHON) -m benchmark.cache_fidelity

auto-calibrate:
	@test -n "$(INPUT)" || (echo "usage: make auto-calibrate INPUT=results/.../summary.json"; exit 1)
	$(PYTHON) -m planner.auto_calibrator --input "$(INPUT)" --out "$${OUT:-results/calibration/recommendation.json}" --report "$${REPORT:-results/calibration/recommendation.md}"

fit-predictor:
	@test -n "$(INPUT)" -a -n "$(MODEL)" -a -n "$(GPU_TYPE)" -a -n "$(BACKEND_ENGINE)" -a -n "$(BACKEND_VERSION)" || (echo "usage: make fit-predictor INPUT=results/state MODEL=... GPU_TYPE=... BACKEND_ENGINE=vllm BACKEND_VERSION=..."; exit 1)
	$(PYTHON) -m planner.ttft_predictor --input "$(INPUT)" --out "$${OUT:-results/calibration/ttft-predictor.json}" --model "$(MODEL)" --gpu-type "$(GPU_TYPE)" --backend-engine "$(BACKEND_ENGINE)" --backend-version "$(BACKEND_VERSION)"

validate-predictor:
	@test -n "$(INPUT)" -a -n "$(ARTIFACT)" || (echo "usage: make validate-predictor INPUT=results/state/held-out ARTIFACT=results/calibration/ttft-predictor.json"; exit 1)
	$(PYTHON) -m planner.ttft_validation --input "$(INPUT)" --artifact "$(ARTIFACT)" --out "$${OUT:-results/calibration/ttft-validation.json}" --report "$${REPORT:-results/calibration/ttft-validation.md}"

policy-evaluation:
	@test -n "$(INPUT)" -a -n "$(EXPERIMENT_ID)" -a -n "$(CONTROL)" -a -n "$(TREATMENT)" || (echo "usage: make policy-evaluation INPUT=results/state EXPERIMENT_ID=... CONTROL=static TREATMENT=kv-v2"; exit 1)
	$(PYTHON) -m planner.policy_evaluation --input "$(INPUT)" --experiment-id "$(EXPERIMENT_ID)" --control "$(CONTROL)" --treatment "$(TREATMENT)" $${SLO_MS:+--slo-ms "$$SLO_MS"} $${MIN_REQUESTS:+--min-requests "$$MIN_REQUESTS"} $${OUT:+--out "$$OUT"} $${REPORT:+--report "$$REPORT"}

vllm-kv-events:
	@test -n "$(BACKEND_ID)" -a -n "$(GENERATION)" -a -n "$(ENDPOINT)" -a -n "$(REPLAY_ENDPOINT)" || (echo "usage: make vllm-kv-events BACKEND_ID=... GENERATION=... ENDPOINT=tcp://127.0.0.1:5557 REPLAY_ENDPOINT=tcp://127.0.0.1:5558"; exit 1)
	$(PYTHON) -m engine_events.vllm --backend-id "$(BACKEND_ID)" --generation "$(GENERATION)" --endpoint "$(ENDPOINT)" --replay-endpoint "$(REPLAY_ENDPOINT)" $${GATEWAY_URL:+--gateway-url "$$GATEWAY_URL"}

vllm-hash-resolver:
	@test -n "$(TOKENIZE_URL)" || (echo "usage: make vllm-hash-resolver TOKENIZE_URL=http://127.0.0.1:8000 BLOCK_SIZE=16 PYTHONHASHSEED=7"; exit 1)
	PYTHONHASHSEED="$${PYTHONHASHSEED:-7}" $(PYTHON) -m engine_events.vllm_hash --tokenize-url "$(TOKENIZE_URL)" --block-size "$${BLOCK_SIZE:-16}" --hash-algo "$${HASH_ALGO:-sha256_cbor}" --python-hash-seed "$${PYTHONHASHSEED:-7}" $${PORT:+--port "$$PORT"}

stage2-local:
	bash scripts/stage2_local_stack.sh run

demo-stage1:
	bash scripts/demo_stage1.sh

demo-kavora: build
	bash scripts/demo_kavora.sh

stage1-gate:
	bash scripts/stage1_gate.sh

research-report:
	bash scripts/research_report.sh
