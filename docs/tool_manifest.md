# Kavora Tool Manifest

Stage 3 tools are admitted only through a versioned manifest. The manifest binds a tool name and version to its SHA-256 artifact digest, input/output JSON schemas, explicit capabilities, and timeout/memory budgets.

The Go gateway validates the contract before dispatch. The Rust worker is intentionally a separate process boundary; a rejected manifest never reaches tool execution. `file`, `network`, `time`, and `memory` capabilities are deny-by-default and must be listed explicitly.
