use kavora_policy::policy_v1::{ChatMessage, EvaluateRequestRequest, ModelRequest, RequestContext};

pub fn valid_request() -> EvaluateRequestRequest {
    EvaluateRequestRequest {
        context: Some(RequestContext {
            request_id: "req-uds".into(),
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
                content_json: br#""hello""#.to_vec(),
                name: None,
            }],
            tools_json: br#"[]"#.to_vec(),
            generation_parameters_json: br#"{}"#.to_vec(),
        }),
    }
}
