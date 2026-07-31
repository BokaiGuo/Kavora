# Stage 2 Results

Run `make benchmark-stage2` to produce the machine-readable matrix and Markdown report under `results/stage2/`. Supplying `KAVORA_STAGE2_REAL_PATHS`, `KAVORA_API_KEY`, and `KAVORA_STAGE2_MODEL` records direct and Gateway measurements against a real backend. Enforced mode is safe-by-default: it only prefers a backend with fresh usable state and otherwise preserves static candidates.
