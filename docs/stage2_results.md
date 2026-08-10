# Stage 2 Results

Stage 2 no longer emits a synthetic proxy scorecard. The public evidence path now requires five real endpoints (`direct`, `static`, `load-aware`, `kv-aware-shadow`, and `kv-aware-enforced`) plus metrics from at least two independent inference backends.

```bash
cp benchmark/config.stage2.template.yaml benchmark/config.stage2.yaml
# Edit model, target URLs, and backend metrics URLs.
make benchmark-stage2-config
make benchmark-stage2
```

The run produces:

```text
results/stage2/stage2_evaluation.json
results/stage2/stage2_evaluation.md
```

The config requires `model_revision` and `backend_version`; the artifact also records Git revision, Python/platform details, GPU/driver information, target URLs, and backend metrics URLs.

Each of the four workloads is repeated at least ten times. The raw artifact keeps per-request TTFT/E2E samples, request IDs, selected backend headers, fallback flags, routing distributions, route switches, error details, and before/after vLLM metric windows. The report aggregates p50/p95/p99 latency, throughput, KV reuse, GPU KV utilization, queue depth, routing switches, fallback count, error rate, and direct-path Gateway overhead with 95% confidence intervals across repetitions.

To reduce cache-warmth order bias, target execution rotates every repetition. With the required five targets and default ten repetitions, every strategy occupies every order position exactly twice; the exact schedule is stored in `execution_schedule`.

`scripts/launch_stage2_vllm_pair.sh` is a local two-replica helper for models that fit twice on the available GPU. It does not hide hardware limitations: users must tune `GPU_MEM_UTIL_PER_REPLICA`, and a one-GPU result remains a single-host result.

`MODEL=/absolute/path/to/local-model make stage2-local` runs the complete local stack. The load-aware Gateway selects the lowest fresh `queue_depth`; the shadow Gateway emits a recommendation without changing the static order; the enforced Gateway may reorder only with usable state. All non-static Gateways poll both Observer `/backend-state` endpoints during the experiment.

No performance claim is stored in this document until a real artifact is checked and reviewed. Missing cache metrics remain `n/a`, shadow recommendations are not described as enforced placement, and enforced routing falls back to static candidates when state is unusable.
