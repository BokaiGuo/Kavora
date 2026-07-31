use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use crate::{
    cache_key::{self, CanonicalMessage, CanonicalRequest},
    config::Config,
    policy_v1::{
        Decision, EvaluateRequestRequest, ModelRequest, PolicyErrorCode, PolicyResult,
        RequestContext,
    },
};

pub struct PolicyEngine {
    config: Config,
    blocked_terms: Vec<String>,
}

impl PolicyEngine {
    pub fn new(config: Config) -> Self {
        let blocked_terms = config
            .blocked_terms
            .iter()
            .map(|term| term.to_lowercase())
            .collect();
        Self {
            config,
            blocked_terms,
        }
    }

    pub fn evaluate(&self, input: &EvaluateRequestRequest) -> PolicyResult {
        let Some(context) = input.context.as_ref() else {
            return self.failure(PolicyErrorCode::InvalidArgument, "missing request context");
        };
        let Some(request) = input.request.as_ref() else {
            return self.failure(PolicyErrorCode::InvalidArgument, "missing model request");
        };

        let max_request_bytes = effective_max_request_bytes(&self.config, context);
        if request_size(request) > max_request_bytes {
            return self.failure(
                PolicyErrorCode::RequestTooLarge,
                "request exceeds configured byte limit",
            );
        }

        match self.evaluate_validated(context, request) {
            Ok(result) => result,
            Err(reason) => self.failure(PolicyErrorCode::InvalidArgument, reason),
        }
    }

    pub(crate) fn config(&self) -> &Config {
        &self.config
    }

    fn evaluate_validated(
        &self,
        context: &RequestContext,
        request: &ModelRequest,
    ) -> Result<PolicyResult, &'static str> {
        if request.model.is_empty() || request.messages.is_empty() {
            return Err("model and messages are required");
        }

        let mut parsed_messages = Vec::with_capacity(request.messages.len());
        for message in &request.messages {
            if message.role.is_empty() {
                return Err("message role is required");
            }
            let content = parse_bounded_json(&message.content_json, self.config.max_json_depth)?;
            parsed_messages.push((message, content));
        }
        let tools = parse_bounded_json(&request.tools_json, self.config.max_json_depth)?;
        let generation_parameters = parse_bounded_json(
            &request.generation_parameters_json,
            self.config.max_json_depth,
        )?;

        let mut text_values = Vec::new();
        for (_, content) in &parsed_messages {
            collect_strings(content, &mut text_values);
        }
        collect_strings(&tools, &mut text_values);
        collect_strings(&generation_parameters, &mut text_values);

        if text_values
            .iter()
            .any(|value| email_regex().is_match(value))
        {
            return Ok(self.blocked(
                PolicyErrorCode::PiiDetected,
                "request contains an email address",
                "pii.email",
                0,
            ));
        }
        for (index, blocked_term) in self.blocked_terms.iter().enumerate() {
            if text_values
                .iter()
                .any(|value| value.to_lowercase().contains(blocked_term))
            {
                return Ok(self.blocked(
                    PolicyErrorCode::ContentBlocked,
                    "request matches a blocked content term",
                    format!("content.term.{index}"),
                    0,
                ));
            }
        }

        let estimated_tokens = request_size(request).div_ceil(4) as u64;
        if context.token_budget > 0 && estimated_tokens > context.token_budget {
            return Ok(PolicyResult {
                estimated_tokens,
                ..self.failure(
                    PolicyErrorCode::TokenBudgetExceeded,
                    "request exceeds token budget",
                )
            });
        }
        let canonical_messages = parsed_messages
            .iter()
            .map(|(message, content)| CanonicalMessage {
                role: &message.role,
                content,
                name: message.name.as_deref(),
            })
            .collect::<Vec<_>>();
        let canonical_request = CanonicalRequest {
            model: &request.model,
            messages: &canonical_messages,
            tools: &tools,
            generation_parameters: &generation_parameters,
        };

        Ok(PolicyResult {
            decision: Decision::Allow.into(),
            error_code: PolicyErrorCode::Unspecified.into(),
            reason: String::new(),
            matched_rule_ids: Vec::new(),
            cache_key: cache_key::calculate(&canonical_request),
            estimated_tokens,
            policy_version: self.config.policy_version.clone(),
        })
    }

    fn failure(&self, code: PolicyErrorCode, reason: impl Into<String>) -> PolicyResult {
        PolicyResult {
            decision: Decision::Block.into(),
            error_code: code.into(),
            reason: reason.into(),
            matched_rule_ids: Vec::new(),
            cache_key: Vec::new(),
            estimated_tokens: 0,
            policy_version: self.config.policy_version.clone(),
        }
    }

    fn blocked(
        &self,
        code: PolicyErrorCode,
        reason: impl Into<String>,
        rule_id: impl Into<String>,
        estimated_tokens: u64,
    ) -> PolicyResult {
        PolicyResult {
            matched_rule_ids: vec![rule_id.into()],
            estimated_tokens,
            ..self.failure(code, reason)
        }
    }
}

pub(crate) fn email_regex() -> &'static Regex {
    static EMAIL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b")
            .expect("email regex must compile")
    });
    &EMAIL
}

pub(crate) fn collect_strings<'a>(value: &'a Value, output: &mut Vec<&'a str>) {
    match value {
        Value::String(value) => output.push(value),
        Value::Array(values) => {
            for value in values {
                collect_strings(value, output);
            }
        }
        Value::Object(values) => {
            for value in values.values() {
                collect_strings(value, output);
            }
        }
        _ => {}
    }
}

fn effective_max_request_bytes(config: &Config, context: &RequestContext) -> usize {
    let context_limit = usize::try_from(context.max_request_bytes).unwrap_or(usize::MAX);
    if context_limit == 0 {
        config.max_request_bytes
    } else {
        config.max_request_bytes.min(context_limit)
    }
}

fn request_size(request: &ModelRequest) -> usize {
    request
        .messages
        .iter()
        .fold(request.model.len(), |size, message| {
            size.saturating_add(message.role.len())
                .saturating_add(message.content_json.len())
                .saturating_add(message.name.as_ref().map_or(0, String::len))
        })
        .saturating_add(request.tools_json.len())
        .saturating_add(request.generation_parameters_json.len())
}

fn parse_bounded_json(encoded: &[u8], max_depth: usize) -> Result<Value, &'static str> {
    if encoded.is_empty() {
        return Err("JSON fields must not be empty");
    }
    let value: Value = serde_json::from_slice(encoded).map_err(|_| "invalid JSON field")?;
    if json_depth(&value) > max_depth {
        return Err("JSON field exceeds configured depth");
    }
    Ok(value)
}

fn json_depth(value: &Value) -> usize {
    match value {
        Value::Array(values) => 1 + values.iter().map(json_depth).max().unwrap_or(0),
        Value::Object(values) => 1 + values.values().map(json_depth).max().unwrap_or(0),
        _ => 1,
    }
}
