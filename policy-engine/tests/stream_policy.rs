use kavora_policy::{
    config::Config,
    policy_v1::{Decision, PolicyErrorCode},
    stream::StreamInspector,
};

#[test]
fn incomplete_event_is_held_until_json_is_complete() {
    let mut inspector = StreamInspector::new(Config::default(), 1024, 4096);
    let event = b"data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n";
    let split = event.len() / 2;

    let first = inspector.inspect(&event[..split]);
    assert_eq!(first.result.decision, Decision::Allow as i32);
    assert_eq!(first.release_bytes, 0);

    let second = inspector.inspect(&event[split..]);
    assert_eq!(second.result.decision, Decision::Allow as i32);
    assert_eq!(second.release_bytes, event.len() as u64);
}

#[test]
fn pii_split_across_transport_chunks_is_blocked_before_release() {
    let mut inspector = StreamInspector::new(Config::default(), 1024, 4096);
    let first = inspector.inspect(b"data: {\"choices\":[{\"delta\":{\"content\":\"alice@");
    assert_eq!(first.release_bytes, 0);

    let second = inspector.inspect(b"example.com\"}}]}\n\n");
    assert_eq!(second.result.decision, Decision::Terminate as i32);
    assert_eq!(
        second.result.error_code,
        PolicyErrorCode::PiiDetected as i32
    );
    assert_eq!(second.release_bytes, 0);
}

#[test]
fn malformed_complete_sse_json_terminates_the_stream() {
    let mut inspector = StreamInspector::new(Config::default(), 1024, 4096);

    let result = inspector.inspect(b"data: {not-json}\n\n");

    assert_eq!(result.result.decision, Decision::Terminate as i32);
    assert_eq!(
        result.result.error_code,
        PolicyErrorCode::ProtocolViolation as i32
    );
    assert_eq!(result.release_bytes, 0);
}

#[test]
fn response_token_budget_terminates_before_release() {
    let mut inspector = StreamInspector::new(Config::default(), 1, 4096);

    let result = inspector
        .inspect(b"data: {\"choices\":[{\"delta\":{\"content\":\"more than one token\"}}]}\n\n");

    assert_eq!(result.result.decision, Decision::Terminate as i32);
    assert_eq!(
        result.result.error_code,
        PolicyErrorCode::TokenBudgetExceeded as i32
    );
    assert_eq!(result.release_bytes, 0);
}

#[test]
fn done_event_releases_normally() {
    let mut inspector = StreamInspector::new(Config::default(), 1024, 4096);

    let result = inspector.inspect(b"data: [DONE]\n\n");
    let finished = inspector.finish();

    assert_eq!(result.release_bytes, 14);
    assert_eq!(finished.result.decision, Decision::Allow as i32);
}
