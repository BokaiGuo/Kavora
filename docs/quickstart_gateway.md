# Kavora Gateway Quickstart

Kavora keeps the real backend contract OpenAI-compatible. The only backend-specific part is the static endpoint/model configuration.

## vLLM

Start vLLM with the existing launcher:

```bash
MODEL=models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0 \
SERVED_MODEL_NAME=kvcache-local-tiny \
bash scripts/launch_vllm.sh
```

Copy `gateway/config.vllm.example.yaml` to a private config, replace the API key, then start the Gateway:

```bash
KAVORA_TENANT_CONFIG=gateway/config.vllm.yaml bash scripts/run_gateway_local.sh
```

The Gateway must be started after the Policy Engine. The vLLM launcher uses port `8000`; the config's model name must match `SERVED_MODEL_NAME`.

## SGLang

```bash
MODEL=models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0 \
SERVED_MODEL_NAME=kvcache-local-tiny \
bash scripts/launch_sglang.sh
KAVORA_TENANT_CONFIG=gateway/config.sglang.yaml bash scripts/run_gateway_local.sh
```

The SGLang launcher uses port `30000` by default. Both backends expose the same `/v1/chat/completions` contract to Kavora.

Run an end-to-end unary and SSE smoke after Policy Engine, the backend, and Gateway are ready:

```bash
KAVORA_API_KEY=replace-with-your-key make smoke-vllm
KAVORA_API_KEY=replace-with-your-key make smoke-sglang
```

The smoke command exits successfully with an explicit `SKIP` when the Gateway is not ready, which is the expected result on a machine without the corresponding GPU/backend environment. Set `KAVORA_SMOKE_REQUIRED=true` in CI or a release gate to turn that skip into a failure.

The existing Python exporter continues to own backend metrics normalization. Gateway `/metrics` owns request-path telemetry; the two namespaces do not overlap.

## Long-running monitoring and advice

Run the exporter alongside the backend. It writes the latest snapshot to `KVCACHE_STATE_DIR` and appends every advice decision to `advice.jsonl`:

```bash
KVCACHE_BACKEND_METRICS_URL=http://127.0.0.1:8000/metrics \
KVCACHE_MODEL_NAME=kvcache-local-tiny \
KVCACHE_STATE_DIR=results/kavora-state \
python3 -m exporter.app
```

Use the live endpoints or CLI:

```bash
curl http://127.0.0.1:9108/backend-state
curl http://127.0.0.1:9108/advice
build/kavora advice --base-url http://127.0.0.1:18000
```

## Showcase demo

The deterministic fake-backend showcase starts Go Gateway and Rust Policy Engine, then demonstrates CLI unary chat, SSE streaming, backend status, and PII rejection:

```bash
make demo-kavora
```

The generated artifact is `results/demo/showcase.json`; the GUI is available at the printed local URL while the demo is running.

## Research report

After Stage 1 and Stage 2 artifacts exist, generate a reproducibility manifest and paper-style report:

```bash
make research-report
```

Outputs include `results/research/research_report.json`, `research_report.md`, and `reproduction_manifest.json`.
