# Stage 2 Promotion Gate

The Stage 2 routing substrate is safe-by-default: backend-state preserves missing/stale quality, shadow evaluation does not reorder traffic, affinity is bounded and tenant-scoped, and enforced mode retains a static fallback. Responses expose `X-Kavora-Backend`, `X-Kavora-Routing-Mode`, `X-Kavora-Routing-Fallback`, and (when available) `X-Kavora-Routing-Suggested` so experiments can audit actual placement against the policy recommendation.

Promotion remains **not granted** until `make benchmark-stage2` completes the configured two-backend, five-target, four-workload matrix with at least ten repetitions and the resulting artifact supports a bounded conclusion. A config validation pass or a controller smoke is not a performance result.
