use std::{
    fs, io,
    os::unix::fs::{FileTypeExt, PermissionsExt},
    path::Path,
};

use tokio::{net::UnixListener, sync::mpsc};
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status};

use crate::{
    policy::PolicyEngine,
    policy_v1::{
        Capability, Decision, EvaluateRequestRequest, EvaluateRequestResponse,
        GetCapabilitiesRequest, GetCapabilitiesResponse, PolicyErrorCode, PolicyResult,
        ProtocolVersion, StreamCheckRequest, StreamCheckResponse,
        policy_service_server::PolicyService, stream_check_request::Payload,
    },
    stream::{StreamInspection, StreamInspector},
};

pub struct PolicyGrpcService {
    engine: PolicyEngine,
    engine_version: String,
}

pub fn bind_uds(path: &Path) -> io::Result<UnixListener> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => fs::remove_file(path)?,
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "refusing to replace a non-socket path",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }

    let listener = UnixListener::bind(path)?;
    if let Err(error) = fs::set_permissions(path, fs::Permissions::from_mode(0o600)) {
        let _ = fs::remove_file(path);
        return Err(error);
    }
    Ok(listener)
}

impl PolicyGrpcService {
    pub fn new(engine: PolicyEngine, engine_version: impl Into<String>) -> Self {
        Self {
            engine,
            engine_version: engine_version.into(),
        }
    }
}

#[tonic::async_trait]
impl PolicyService for PolicyGrpcService {
    async fn get_capabilities(
        &self,
        request: Request<GetCapabilitiesRequest>,
    ) -> Result<Response<GetCapabilitiesResponse>, Status> {
        let client_protocol = request
            .into_inner()
            .client_protocol
            .ok_or_else(|| Status::invalid_argument("client_protocol is required"))?;
        if client_protocol.major != 1 {
            return Err(Status::failed_precondition(
                "unsupported protocol major version",
            ));
        }

        Ok(Response::new(GetCapabilitiesResponse {
            server_protocol: Some(ProtocolVersion { major: 1, minor: 0 }),
            engine_version: self.engine_version.clone(),
            capabilities: vec![
                Capability::RequestPolicy.into(),
                Capability::StreamPolicy.into(),
                Capability::IncrementalJson.into(),
                Capability::TokenBudget.into(),
                Capability::CacheKey.into(),
            ],
        }))
    }

    async fn evaluate_request(
        &self,
        request: Request<EvaluateRequestRequest>,
    ) -> Result<Response<EvaluateRequestResponse>, Status> {
        let context = request.get_ref().context.as_ref();
        let result = self.engine.evaluate(request.get_ref());
        audit_policy_event(
            context
                .map(|value| value.request_id.as_str())
                .unwrap_or_default(),
            context
                .map(|value| value.tenant_id.as_str())
                .unwrap_or_default(),
            "policy_evaluated",
            result.decision,
        );
        Ok(Response::new(EvaluateRequestResponse {
            result: Some(result),
        }))
    }

    type CheckStreamStream = ReceiverStream<Result<StreamCheckResponse, Status>>;

    async fn check_stream(
        &self,
        request: Request<tonic::Streaming<StreamCheckRequest>>,
    ) -> Result<Response<Self::CheckStreamStream>, Status> {
        let mut inbound = request.into_inner();
        let open_message = inbound
            .message()
            .await?
            .ok_or_else(|| Status::invalid_argument("stream must start with open"))?;
        let request_id = open_message.request_id.clone();
        if request_id.is_empty() || open_message.sequence != 0 {
            return Err(Status::invalid_argument(
                "stream open requires request_id and sequence zero",
            ));
        }
        let Payload::Open(open) = open_message
            .payload
            .ok_or_else(|| Status::invalid_argument("stream open payload is required"))?
        else {
            return Err(Status::invalid_argument(
                "first stream payload must be open",
            ));
        };
        let context = open
            .context
            .ok_or_else(|| Status::invalid_argument("stream context is required"))?;
        let model_request = open
            .request
            .ok_or_else(|| Status::invalid_argument("stream model request is required"))?;
        if context.request_id != request_id {
            return Err(Status::invalid_argument("stream request IDs do not match"));
        }

        let open_result = self.engine.evaluate(&EvaluateRequestRequest {
            context: Some(context.clone()),
            request: Some(model_request),
        });
        audit_policy_event(
            &request_id,
            &context.tenant_id,
            "stream_policy_opened",
            open_result.decision,
        );
        let (sender, receiver) = mpsc::channel(8);
        sender
            .send(Ok(StreamCheckResponse {
                request_id: request_id.clone(),
                sequence: 0,
                result: Some(open_result.clone()),
                inspected_bytes: 0,
                consumed_tokens: open_result.estimated_tokens,
                release_bytes: 0,
            }))
            .await
            .map_err(|_| Status::cancelled("stream consumer disconnected"))?;
        if open_result.decision != Decision::Allow as i32 {
            drop(sender);
            return Ok(Response::new(ReceiverStream::new(receiver)));
        }

        let max_response_bytes = if context.max_response_bytes == 0 {
            self.engine.config().max_request_bytes as u64
        } else {
            context.max_response_bytes
        };
        let response_token_budget = if context.token_budget == 0 {
            0
        } else {
            context
                .token_budget
                .saturating_sub(open_result.estimated_tokens)
        };
        let mut inspector = StreamInspector::new(
            self.engine.config().clone(),
            response_token_budget,
            max_response_bytes,
        );
        let policy_version = self.engine.config().policy_version.clone();
        tokio::spawn(async move {
            let mut expected_sequence = 1u64;
            loop {
                let message = match inbound.message().await {
                    Ok(Some(message)) => message,
                    Ok(None) => {
                        let inspection = inspector.finish();
                        let _ = sender
                            .send(Ok(stream_response(
                                &request_id,
                                expected_sequence,
                                inspection,
                            )))
                            .await;
                        break;
                    }
                    Err(status) => {
                        let _ = sender.send(Err(status)).await;
                        break;
                    }
                };
                if message.request_id != request_id || message.sequence != expected_sequence {
                    let _ = sender
                        .send(Ok(protocol_violation(
                            &request_id,
                            expected_sequence,
                            &policy_version,
                            "stream request ID or sequence mismatch",
                        )))
                        .await;
                    break;
                }

                let should_close = matches!(message.payload, Some(Payload::Close(_)));
                let inspection = match message.payload {
                    Some(Payload::Chunk(chunk)) => inspector.inspect(&chunk.data),
                    Some(Payload::Close(_)) => inspector.finish(),
                    _ => {
                        let _ = sender
                            .send(Ok(protocol_violation(
                                &request_id,
                                expected_sequence,
                                &policy_version,
                                "stream payload must be chunk or close",
                            )))
                            .await;
                        break;
                    }
                };
                let decision = inspection.result.decision;
                if sender
                    .send(Ok(stream_response(
                        &request_id,
                        expected_sequence,
                        inspection,
                    )))
                    .await
                    .is_err()
                {
                    break;
                }
                if should_close || decision != Decision::Allow as i32 {
                    break;
                }
                expected_sequence = expected_sequence.saturating_add(1);
            }
        });

        Ok(Response::new(ReceiverStream::new(receiver)))
    }
}

fn audit_policy_event(request_id: &str, tenant_id: &str, event: &str, decision: i32) {
    let record = serde_json::json!({
        "event": event,
        "request_id": request_id,
        "tenant_id": tenant_id,
        "policy_decision": decision,
    });
    eprintln!("{record}");
}

fn stream_response(
    request_id: &str,
    sequence: u64,
    inspection: StreamInspection,
) -> StreamCheckResponse {
    StreamCheckResponse {
        request_id: request_id.into(),
        sequence,
        result: Some(inspection.result),
        inspected_bytes: inspection.inspected_bytes,
        consumed_tokens: inspection.consumed_tokens,
        release_bytes: inspection.release_bytes,
    }
}

fn protocol_violation(
    request_id: &str,
    sequence: u64,
    policy_version: &str,
    reason: &str,
) -> StreamCheckResponse {
    StreamCheckResponse {
        request_id: request_id.into(),
        sequence,
        result: Some(PolicyResult {
            decision: Decision::Terminate.into(),
            error_code: PolicyErrorCode::ProtocolViolation.into(),
            reason: reason.into(),
            matched_rule_ids: Vec::new(),
            cache_key: Vec::new(),
            estimated_tokens: 0,
            policy_version: policy_version.into(),
        }),
        inspected_bytes: 0,
        consumed_tokens: 0,
        release_bytes: 0,
    }
}
