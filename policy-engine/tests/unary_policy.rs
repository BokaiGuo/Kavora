use kavora_policy::{
    config::Config,
    policy::PolicyEngine,
    policy_v1::{ChatMessage, Decision, EvaluateRequestRequest, ModelRequest, RequestContext},
};

#[test]
fn semantic_json_order_produces_the_same_cache_key() {
    let engine = PolicyEngine::new(Config::default());
    let first = request_with_content(br#"{"text":"hello","metadata":{"a":1,"b":2}}"#);
    let second = request_with_content(br#"{"metadata":{"b":2,"a":1},"text":"hello"}"#);

    let first_result = engine.evaluate(&first);
    let second_result = engine.evaluate(&second);

    assert_eq!(first_result.decision, Decision::Allow as i32);
    assert_eq!(second_result.decision, Decision::Allow as i32);
    assert!(!first_result.cache_key.is_empty());
    assert_eq!(first_result.cache_key, second_result.cache_key);
}

#[test]
fn email_address_is_blocked_with_stable_policy_code() {
    let engine = PolicyEngine::new(Config::default());
    let request = request_with_content(br#""contact alice@example.com""#);

    let result = engine.evaluate(&request);

    assert_eq!(result.decision, Decision::Block as i32);
    assert_eq!(
        result.error_code,
        kavora_policy::policy_v1::PolicyErrorCode::PiiDetected as i32
    );
    assert_eq!(result.matched_rule_ids, ["pii.email"]);
    assert!(result.cache_key.is_empty());
}

#[test]
fn configured_content_term_is_blocked_case_insensitively() {
    let config = Config {
        blocked_terms: vec!["forbidden topic".into()],
        ..Config::default()
    };
    let engine = PolicyEngine::new(config);
    let request = request_with_content(br#""Discuss FORBIDDEN TOPIC now""#);

    let result = engine.evaluate(&request);

    assert_eq!(result.decision, Decision::Block as i32);
    assert_eq!(
        result.error_code,
        kavora_policy::policy_v1::PolicyErrorCode::ContentBlocked as i32
    );
    assert_eq!(result.matched_rule_ids, ["content.term.0"]);
}

#[test]
fn token_budget_is_enforced_before_backend_dispatch() {
    let engine = PolicyEngine::new(Config::default());
    let mut request = request_with_content(br#""this request is larger than one token""#);
    request.context.as_mut().unwrap().token_budget = 1;

    let result = engine.evaluate(&request);

    assert_eq!(result.decision, Decision::Block as i32);
    assert_eq!(
        result.error_code,
        kavora_policy::policy_v1::PolicyErrorCode::TokenBudgetExceeded as i32
    );
    assert!(result.estimated_tokens > 1);
}

#[test]
fn oversized_request_is_rejected_before_json_parsing() {
    let config = Config {
        max_request_bytes: 32,
        ..Config::default()
    };
    let engine = PolicyEngine::new(config);
    let request = request_with_content(br#""this input is intentionally larger than the limit""#);

    let result = engine.evaluate(&request);

    assert_eq!(
        result.error_code,
        kavora_policy::policy_v1::PolicyErrorCode::RequestTooLarge as i32
    );
}

#[test]
fn deeply_nested_json_is_rejected_at_the_configured_depth() {
    let config = Config {
        max_json_depth: 4,
        ..Config::default()
    };
    let engine = PolicyEngine::new(config);
    let request = request_with_content(br#"[[[[["too deep"]]]]]"#);

    let result = engine.evaluate(&request);

    assert_eq!(
        result.error_code,
        kavora_policy::policy_v1::PolicyErrorCode::InvalidArgument as i32
    );
    assert!(result.reason.contains("depth"));
}

fn request_with_content(content_json: &[u8]) -> EvaluateRequestRequest {
    EvaluateRequestRequest {
        context: Some(RequestContext {
            request_id: "req-1".into(),
            tenant_id: "tenant-a".into(),
            policy_version: "policy-v1".into(),
            max_request_bytes: 4096,
            token_budget: 1024,
            ..RequestContext::default()
        }),
        request: Some(ModelRequest {
            model: "demo-model".into(),
            messages: vec![ChatMessage {
                role: "user".into(),
                content_json: content_json.to_vec(),
                name: None,
            }],
            tools_json: br#"[]"#.to_vec(),
            generation_parameters_json: br#"{"temperature":0,"top_p":1}"#.to_vec(),
        }),
    }
}
