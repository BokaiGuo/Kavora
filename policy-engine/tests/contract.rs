use kavora_policy::policy_v1::{
    ChatMessage, EvaluateRequestRequest, FailMode, ModelRequest, RequestContext,
};
use prost::Message;

#[test]
fn evaluate_request_matches_golden_wire_format() {
    let want = hex::decode(
        include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../proto/testdata/evaluate_request.hex"
        ))
        .trim(),
    )
    .expect("golden fixture must contain valid hex");

    let message = EvaluateRequestRequest {
        context: Some(RequestContext {
            request_id: "req-123".into(),
            tenant_id: "tenant-a".into(),
            traceparent: String::new(),
            tracestate: String::new(),
            policy_version: "policy-v1".into(),
            fail_mode: FailMode::Closed.into(),
            deadline_unix_millis: 42,
            max_request_bytes: 1024,
            max_response_bytes: 2048,
            token_budget: 128,
        }),
        request: Some(ModelRequest {
            model: "demo-model".into(),
            messages: vec![ChatMessage {
                role: "user".into(),
                content_json: br#""hello""#.to_vec(),
                name: None,
            }],
            tools_json: br#"[]"#.to_vec(),
            generation_parameters_json: br#"{"temperature":0}"#.to_vec(),
        }),
    };

    let got = message.encode_to_vec();
    let decoded = EvaluateRequestRequest::decode(got.as_slice())
        .expect("generated Rust contract must decode its own wire format");

    assert_eq!(decoded, message);
    assert_eq!(got, want, "protobuf field numbers or encoding changed");
}
