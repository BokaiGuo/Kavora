# Kavora Task Checklist

## Stage 1: Bilingual Gateway

- [x] Task 0: Establish Go toolchain gate
- [x] Task 1: Create polyglot workspace skeleton
- [x] Task 2: Define versioned policy contract
- [x] Task 3: Build deterministic fake backend
- [x] Task 4: Implement Rust unary policy
- [x] Task 5: Deliver unary vertical slice
- [x] Checkpoint A: Review unary contract
- [x] Task 6: Add compatible SSE proxying
- [x] Task 7: Add stream policy and backpressure
- [x] Task 8: Add tenant authentication and limits
- [x] Task 9: Add backend registry and failover
- [x] Checkpoint B: Review production-shaped path
- [x] Task 10: Wire telemetry and audit
- [x] Task 11: Integrate vLLM and SGLang
- [x] Task 12: Build benchmark and failure demo
- [x] Stage 1 promotion gate (real vLLM smoke passed; durable report may be refreshed with `make stage1-gate`)

## Stage 2: KV-aware Routing

- [x] Task 13: Define backend-state contract
- [x] Task 14: Implement shadow evaluation
- [x] Task 15: Add bounded prefix affinity
- [x] Task 16: Add routing safety controls
- [ ] Task 17: Run reproducible real-backend experiment matrix (runner complete; real artifact pending)
- [x] Stage 2 promotion gate (safe substrate passed; enforced mode remains no-go)

## Stage 3: Secure Agent Runtime

- [x] Task 18: Define tool execution contract
- [x] Task 19: Build Rust Wasmtime worker (digest-verified, no host capabilities)
- [x] Task 20: Add Go agent loop
- [x] Task 21: Add deterministic replay
- [x] Final project checkpoint
