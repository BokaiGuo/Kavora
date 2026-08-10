# Stage 7: Causal Policy Evaluation

Stage 7 changes the control-plane question from “which policy scores better?” to:

```text
did the assigned policy cause a measurable improvement under a credible online experiment?
```

## Experiment controller

Set `KAVORA_EXPERIMENT_CONFIG=gateway/experiment.example.yaml`. Each decision records:

- experiment ID and assigned policy;
- assignment unit, probability, seed, and window;
- warmup and carryover guards;
- whether the experiment is active or stopped;
- the routing decision, actual backend, and realized outcome inherited from Stage 6.

Two designs are supported:

- `switchback`: a fixed-seed policy assignment per time window, with warmup and cooldown exclusion zones;
- `isolated-pool`: deterministic request assignment where each arm is restricted to its configured backend pool.

The controller accepts `static`, `load-aware`, `kv-v1`, and `kv-v2`. Online `kv-v2` retains cache affinity only when its estimated prefill benefit exceeds the additional queue penalty.

```yaml
experiment:
  id: kv-v2-vs-static-001
  control: {policy: static}
  treatment: {policy: kv-v2}
  design:
    type: switchback
    window: 5m
    warmup: 30s
    cooldown: 30s
    seed: kavora-stage7-001
    treatment_probability: 0.5
    start_at: 2026-08-10T00:00:00Z
  stop:
    min_requests: 5000
    max_duration: 2h
```

Inspect the active definition through:

```http
GET /v1/admin/experiment
```

## vLLM request hash alignment

For exact native-event placement, run vLLM with a fixed `PYTHONHASHSEED`, `sha256_cbor`, the same block size on every worker, KV event publishing, and replay enabled. Start the resolver:

```bash
make vllm-hash-resolver \
  TOKENIZE_URL=http://127.0.0.1:8000 \
  BLOCK_SIZE=16 \
  PYTHONHASHSEED=7
```

Then set:

```bash
export KAVORA_CACHE_FIDELITY=exact
export KAVORA_VLLM_HASH_RESOLVER_URL=http://127.0.0.1:19120
```

The resolver calls the serving backend's `/tokenize` endpoint and uses vLLM's own block-hash implementation. The Go gateway passes the ordered external block keys to the exact provider, which measures the longest resident prefix. `X-Kavora-Hash-Alignment` reports `vllm-exact` or `unavailable`.

This exact path currently covers text-only requests whose cache identity depends only on the token sequence. Requests using `cache_salt`, prompt embeddings, LoRA identity, or non-text multimodal hash inputs are rejected by the resolver instead of being mislabeled as exact. The resolver, vLLM workers, and KV-event subscribers must share the same vLLM-compatible block size, hash algorithm, and fixed `PYTHONHASHSEED`.

## Policy evaluation

Generate JSON and Markdown from the append-only decision/outcome journals:

```bash
make policy-evaluation \
  INPUT=results/state \
  EXPERIMENT_ID=kv-v2-vs-static-001 \
  CONTROL=static \
  TREATMENT=kv-v2 \
  SLO_MS=500 \
  MIN_REQUESTS=5000
```

The report includes:

- control/treatment request counts, TTFT means and p95;
- average treatment effect and relative difference;
- window-cluster bootstrap 95% confidence interval for switchbacks;
- SLO violation, throughput, error, fallback, and prediction-drift checks;
- assignment, warmup exclusion, window balance, and contamination checks;
- short/long prompt, low/high reuse, and low/high load strata;
- `PROMOTION_ELIGIBLE` only when effect, safety, integrity, and minimum-sample gates pass.

Lifecycle configuration can require the causal gate:

```yaml
gates:
  require_experiment_promotion: true
```

## Held-out predictor validation

Use a journal directory from a time window that was not used for fitting:

```bash
make validate-predictor \
  INPUT=results/state/held-out \
  ARTIFACT=results/calibration/ttft-predictor.json
```

This produces `ttft-validation.json` and `.md` with MAE, p95 absolute error, signed bias, sample exclusions, and a dimension-scoped pass/fail result.

## Claim boundaries

- Switchbacks reduce shared queue/cache interference but do not prove interference is absent.
- The cluster bootstrap estimates uncertainty across observed windows; it is not a universal causal guarantee.
- Exact request hashing is implemented and protocol-tested, but the repository still lacks a reviewed real-GPU Stage 2 artifact demonstrating its production performance impact.
- Held-out validation tooling is implemented; a held-out real-GPU journal is still required before claiming predictor generalization.
