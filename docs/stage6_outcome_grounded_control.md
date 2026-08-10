# Stage 6: Outcome-Grounded Control

Stage 6 closes the control loop:

```text
observe -> predict -> decide -> execute -> record outcome -> audit error -> recalibrate / roll back
```

## Decision + Outcome Ledger

Every completed routed request can attach:

- actual backend, status and success;
- user-observed TTFT and end-to-end latency;
- prompt/output token counts;
- model, GPU type, backend engine and backend version dimensions;
- observed cache hit ratio and matched tokens when the backend exports them;
- signed and absolute TTFT prediction error.

The hot ledger remains bounded. The durable layer defaults to append-only files under `results/state/`:

```text
decisions-YYYY-MM-DD.jsonl
outcomes-YYYY-MM-DD.jsonl
```

Set `KAVORA_DECISION_JOURNAL_DIR=off` to disable persistence or point it at another state directory. Startup replays all available journal files into the bounded in-memory index.

## Explainable predictor fitting

Fit a non-negative linear predictor from outcome-grounded samples:

```bash
make fit-predictor \
  INPUT=results/state \
  MODEL=qwen3-8b \
  GPU_TYPE=RTX-4090 \
  BACKEND_ENGINE=vllm \
  BACKEND_VERSION=0.10
```

The model remains explicit:

```text
TTFT = intercept
     + uncached_tokens * uncached_token_ms
     + cached_tokens * cached_token_ms
     + queue_depth * queue_penalty_ms
     + kv_pressure * kv_pressure_penalty_ms
```

Only outcomes carrying `observed_matched_tokens` are eligible. Load an artifact with `KAVORA_TTFT_PREDICTOR_PATH`. Decisions record `predictor_version` so historical errors remain attributable.

Backend entries should expose `gpu_type`, `engine`, and `engine_version` attributes. The fitter rejects successful samples whose recorded dimensions conflict with the requested artifact dimensions.

For realized observations, backends may return:

- `X-Kavora-Cache-Hit-Ratio` between 0 and 1;
- `X-Kavora-Matched-Tokens` as a non-negative integer;
- `X-Kavora-TTFT-MS` as a positive backend-measured TTFT for buffered responses;
- `X-Kavora-Output-Tokens` for streaming responses where an OpenAI `usage` object is unavailable.

For streaming responses, Kavora can measure user-observed first-byte TTFT directly. A buffered response without `X-Kavora-TTFT-MS` records TTFT as unavailable instead of relabeling full-response latency as TTFT.

The current fitter reports in-sample clamped-ridge validation. Loaded artifacts are applied only when model, GPU type, backend engine, and backend version match exactly; other candidates retain the default heuristic predictor. This is calibration evidence, not held-out production generalization evidence.

## Prediction quality and drift

```http
GET /v1/admin/prediction-quality?limit=500&slo_ms=500
```

The API and GUI report MAE, p95 absolute error, signed bias, SLO probability buckets, and TTFT error grouped by evidence quality. Lifecycle configurations may set:

```yaml
gates:
  max_prediction_mae_ms: 25
  max_evidence_error_rate: 0.10
```

A violated prediction/evidence gate rolls the policy back to `static` and clears approval.

## Native vLLM KV events

Kavora implements the vLLM ZMQ transport contract:

- SUB frames: topic, 8-byte sequence, msgpack `EventBatch`;
- DEALER-to-ROUTER replay requests from the first missing sequence;
- duplicate suppression and durable subscriber checkpoints;
- `BlockStored`, `BlockRemoved`, and `AllBlocksCleared` semantics;
- automatic reset on sequence restart plus explicit backend generation identifiers.

Run:

```bash
make vllm-kv-events \
  BACKEND_ID=gpu-0 \
  GENERATION="$(cat /proc/sys/kernel/random/uuid)" \
  ENDPOINT=tcp://127.0.0.1:5557 \
  REPLAY_ENDPOINT=tcp://127.0.0.1:5558
```

The sidecar posts normalized events to `/v1/admin/cache-events`. Configure vLLM with KV event publishing and a replay buffer.

**Hash-alignment boundary:** native events use `vllm:block:<external_block_hash>`. Exact request placement requires the request cache key to be generated in that same namespace. The transport and cache lifecycle are implemented; Kavora does not claim tokenizer/hash alignment for ordinary OpenAI requests yet.

## Policy laboratory

A trace can evaluate multiple policies in one deterministic run:

```bash
build/kavora replay benchmark/workload_trace.example.jsonl \
  --policy static \
  --policy load-aware \
  --policy kv-v1 \
  --policy kv-v2 \
  --evidence-quality strict
```

`kv-v2` rejects cache affinity when the estimated queue penalty exceeds the simulated prefill benefit. These are simulator comparisons, not counterfactual production measurements. Randomized online policy assignment remains future work and must record assignment probability plus realized outcomes.
