use serde_json::Value;

use crate::{
    config::Config,
    policy::{collect_strings, email_regex},
    policy_v1::{Decision, PolicyErrorCode, PolicyResult},
};

pub struct StreamInspection {
    pub result: PolicyResult,
    pub release_bytes: u64,
    pub inspected_bytes: u64,
    pub consumed_tokens: u64,
}

pub struct StreamInspector {
    config: Config,
    blocked_terms: Vec<String>,
    pending: Vec<u8>,
    inspected_bytes: u64,
    semantic_bytes: u64,
    token_budget: u64,
    max_response_bytes: u64,
    saw_done: bool,
    terminal: Option<PolicyResult>,
}

impl StreamInspector {
    pub fn new(config: Config, token_budget: u64, max_response_bytes: u64) -> Self {
        let blocked_terms = config
            .blocked_terms
            .iter()
            .map(|term| term.to_lowercase())
            .collect();
        Self {
            config,
            blocked_terms,
            pending: Vec::new(),
            inspected_bytes: 0,
            semantic_bytes: 0,
            token_budget,
            max_response_bytes,
            saw_done: false,
            terminal: None,
        }
    }

    pub fn inspect(&mut self, chunk: &[u8]) -> StreamInspection {
        if let Some(result) = &self.terminal {
            return self.inspection(result.clone(), 0);
        }
        self.inspected_bytes = self.inspected_bytes.saturating_add(chunk.len() as u64);
        if self.inspected_bytes > self.max_response_bytes {
            return self.terminate(
                PolicyErrorCode::RequestTooLarge,
                "response exceeds configured byte limit",
            );
        }
        self.pending.extend_from_slice(chunk);

        let mut release_bytes = 0usize;
        while let Some(event_end) = find_event_end(&self.pending[release_bytes..]) {
            let absolute_end = release_bytes + event_end;
            let event = self.pending[release_bytes..absolute_end].to_vec();
            if let Err((code, reason)) = self.inspect_event(&event) {
                return self.terminate(code, reason);
            }
            release_bytes = absolute_end;
        }

        if release_bytes > 0 {
            self.pending.drain(..release_bytes);
        }
        self.inspection(self.allow(), release_bytes as u64)
    }

    pub fn finish(&mut self) -> StreamInspection {
        if let Some(result) = &self.terminal {
            return self.inspection(result.clone(), 0);
        }
        if self.saw_done && self.pending.iter().all(u8::is_ascii_whitespace) {
            let release_bytes = self.pending.len() as u64;
            self.pending.clear();
            return self.inspection(self.allow(), release_bytes);
        }
        self.terminate(
            PolicyErrorCode::ProtocolViolation,
            "stream ended with an incomplete SSE event",
        )
    }

    fn inspect_event(&mut self, event: &[u8]) -> Result<(), (PolicyErrorCode, &'static str)> {
        let data = event_data(event)?;
        if data.is_empty() {
            return Ok(());
        }
        if data == b"[DONE]" {
            self.saw_done = true;
            return Ok(());
        }
        if self.saw_done {
            return Err((
                PolicyErrorCode::ProtocolViolation,
                "SSE data appeared after DONE",
            ));
        }
        let value: Value = serde_json::from_slice(&data).map_err(|_| {
            (
                PolicyErrorCode::ProtocolViolation,
                "SSE data is not valid JSON",
            )
        })?;
        if json_depth(&value) > self.config.max_json_depth {
            return Err((
                PolicyErrorCode::ProtocolViolation,
                "SSE JSON exceeds configured depth",
            ));
        }

        let mut strings = Vec::new();
        collect_strings(&value, &mut strings);
        if strings.iter().any(|value| email_regex().is_match(value)) {
            return Err((
                PolicyErrorCode::PiiDetected,
                "response contains an email address",
            ));
        }
        for blocked_term in &self.blocked_terms {
            if strings
                .iter()
                .any(|value| value.to_lowercase().contains(blocked_term))
            {
                return Err((
                    PolicyErrorCode::ContentBlocked,
                    "response matches a blocked content term",
                ));
            }
        }

        self.semantic_bytes = self.semantic_bytes.saturating_add(
            strings
                .iter()
                .fold(0u64, |size, value| size.saturating_add(value.len() as u64)),
        );
        let consumed_tokens = self.consumed_tokens();
        if self.token_budget > 0 && consumed_tokens > self.token_budget {
            return Err((
                PolicyErrorCode::TokenBudgetExceeded,
                "response exceeds token budget",
            ));
        }
        Ok(())
    }

    fn terminate(&mut self, code: PolicyErrorCode, reason: &'static str) -> StreamInspection {
        let result = PolicyResult {
            decision: Decision::Terminate.into(),
            error_code: code.into(),
            reason: reason.into(),
            matched_rule_ids: Vec::new(),
            cache_key: Vec::new(),
            estimated_tokens: self.consumed_tokens(),
            policy_version: self.config.policy_version.clone(),
        };
        self.terminal = Some(result.clone());
        self.inspection(result, 0)
    }

    fn allow(&self) -> PolicyResult {
        PolicyResult {
            decision: Decision::Allow.into(),
            error_code: PolicyErrorCode::Unspecified.into(),
            reason: String::new(),
            matched_rule_ids: Vec::new(),
            cache_key: Vec::new(),
            estimated_tokens: self.consumed_tokens(),
            policy_version: self.config.policy_version.clone(),
        }
    }

    fn inspection(&self, result: PolicyResult, release_bytes: u64) -> StreamInspection {
        StreamInspection {
            result,
            release_bytes,
            inspected_bytes: self.inspected_bytes,
            consumed_tokens: self.consumed_tokens(),
        }
    }

    fn consumed_tokens(&self) -> u64 {
        self.semantic_bytes.div_ceil(4)
    }
}

fn find_event_end(input: &[u8]) -> Option<usize> {
    input
        .windows(2)
        .position(|window| window == b"\n\n")
        .map(|index| index + 2)
        .or_else(|| {
            input
                .windows(4)
                .position(|window| window == b"\r\n\r\n")
                .map(|index| index + 4)
        })
}

fn event_data(event: &[u8]) -> Result<Vec<u8>, (PolicyErrorCode, &'static str)> {
    let text = std::str::from_utf8(event)
        .map_err(|_| (PolicyErrorCode::ProtocolViolation, "SSE event is not UTF-8"))?;
    let mut data_lines = Vec::new();
    for line in text.lines() {
        let line = line.strip_suffix('\r').unwrap_or(line);
        if let Some(data) = line.strip_prefix("data:") {
            data_lines.push(data.strip_prefix(' ').unwrap_or(data));
        }
    }
    Ok(data_lines.join("\n").into_bytes())
}

fn json_depth(value: &Value) -> usize {
    match value {
        Value::Array(values) => 1 + values.iter().map(json_depth).max().unwrap_or(0),
        Value::Object(values) => 1 + values.values().map(json_depth).max().unwrap_or(0),
        _ => 1,
    }
}
