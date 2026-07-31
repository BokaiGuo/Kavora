# Stage 3 Secure Agent Runtime

Kavora now has the stable boundaries for a secure runtime: versioned tool manifests, deterministic bounded agent steps, JSONL replay events, and an isolated Rust worker process. The worker executes only digest-matched modules with the narrow `run() -> i32` ABI. It denies host capabilities other than the inert `compute` capability, applies memory/table/instance limits, consumes deterministic fuel, and interrupts at the manifest timeout. No WASI imports are linked, so file/network/time access is unavailable by construction.
