use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{self, BufRead};
use std::thread;
use std::time::Duration;
use wasmtime::{Config, Engine, Instance, Module, Store, StoreLimitsBuilder};

#[derive(Deserialize)]
struct Invocation {
    request_id: String,
    tool: String,
    manifest_sha256: String,
    input_json: String,
    wasm_base64: String,
    timeout_millis: u64,
    memory_bytes: usize,
    capabilities: Vec<String>,
}
#[derive(Serialize)]
struct Response {
    request_id: String,
    status: String,
    output_json: String,
    error: Option<String>,
}

fn main() {
    for line in io::stdin().lock().lines() {
        let response = match line {
            Ok(line) => match serde_json::from_str::<Invocation>(&line) {
                Ok(invocation) => execute(invocation),
                Err(error) => Response {
                    request_id: String::new(),
                    status: "invalid_request".into(),
                    output_json: "null".into(),
                    error: Some(error.to_string()),
                },
            },
            Err(error) => Response {
                request_id: String::new(),
                status: "io_error".into(),
                output_json: "null".into(),
                error: Some(error.to_string()),
            },
        };
        println!(
            "{}",
            serde_json::to_string(&response)
                .unwrap_or_else(|_| "{\"status\":\"internal_error\"}".into())
        );
    }
}

fn execute(invocation: Invocation) -> Response {
    let request_id = invocation.request_id.clone();
    if invocation.timeout_millis == 0 || invocation.memory_bytes == 0 {
        return failure(
            request_id,
            "invalid_limits",
            "timeout_millis and memory_bytes must be positive".into(),
        );
    }
    if let Some(capability) = invocation
        .capabilities
        .iter()
        .find(|capability| capability.as_str() != "compute")
    {
        return failure(
            request_id,
            "capability_denied",
            format!("capability {capability} is not available in the worker"),
        );
    }
    let bytes = match decode_base64(&invocation.wasm_base64) {
        Ok(bytes) => bytes,
        Err(error) => return failure(request_id, "invalid_wasm", error),
    };
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    if hex::encode(hasher.finalize()) != invocation.manifest_sha256 {
        return failure(
            request_id,
            "manifest_mismatch",
            "WASM digest does not match manifest".into(),
        );
    }
    let mut config = Config::new();
    config.epoch_interruption(true);
    config.consume_fuel(true);
    let engine = match Engine::new(&config) {
        Ok(engine) => engine,
        Err(error) => return failure(request_id, "engine_error", error.to_string()),
    };
    let module = match Module::new(&engine, &bytes) {
        Ok(module) => module,
        Err(error) => return failure(request_id, "invalid_wasm", error.to_string()),
    };
    let limits = StoreLimitsBuilder::new()
        .memory_size(invocation.memory_bytes)
        .instances(1)
        .tables(4)
        .memories(1)
        .trap_on_grow_failure(true)
        .build();
    let mut store = Store::new(&engine, limits);
    store.limiter(|limits| limits);
    let _ = store.set_fuel(invocation.timeout_millis.saturating_mul(100_000));
    store.set_epoch_deadline(1);
    let ticker_engine = engine.clone();
    let timeout = Duration::from_millis(invocation.timeout_millis);
    thread::spawn(move || {
        thread::sleep(timeout);
        ticker_engine.increment_epoch();
    });
    let instance = match Instance::new(&mut store, &module, &[]) {
        Ok(instance) => instance,
        Err(error) => return failure(request_id, "instantiation_failed", error.to_string()),
    };
    let run = match instance.get_typed_func::<(), i32>(&mut store, "run") {
        Ok(run) => run,
        Err(error) => {
            return failure(
                request_id,
                "contract_rejected",
                format!("tool {} must export run() -> i32: {error}", invocation.tool),
            );
        }
    };
    match run.call(&mut store, ()) {
        Ok(value) => Response {
            request_id,
            status: "completed".into(),
            output_json: serde_json::json!({"result": value, "input": invocation.input_json})
                .to_string(),
            error: None,
        },
        Err(error) => failure(request_id, "trap", error.to_string()),
    }
}

fn failure(request_id: String, status: &str, error: String) -> Response {
    Response {
        request_id,
        status: status.into(),
        output_json: "null".into(),
        error: Some(error),
    }
}

fn decode_base64(value: &str) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    let mut buffer = 0u32;
    let mut bits = 0u8;
    for byte in value.bytes() {
        let digit = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            _ => return Err("invalid base64".into()),
        };
        buffer = (buffer << 6) | digit as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            output.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    Ok(output)
}
