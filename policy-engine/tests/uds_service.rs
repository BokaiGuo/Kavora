use std::{os::unix::fs::PermissionsExt, time::Duration};

use hyper_util::rt::TokioIo;
use kavora_policy::{
    config::Config,
    policy::PolicyEngine,
    policy_v1::{
        Capability, Decision, GetCapabilitiesRequest, ProtocolVersion, StreamCheckRequest,
        StreamChunk, StreamClose, StreamOpen, policy_service_client::PolicyServiceClient,
        policy_service_server::PolicyServiceServer, stream_check_request::Payload,
    },
    service::{PolicyGrpcService, bind_uds},
};
use tempfile::tempdir;
use tokio::net::UnixStream;
use tokio_stream::wrappers::UnixListenerStream;
use tonic::transport::{Endpoint, Server};
use tower::service_fn;

mod support;

#[tokio::test]
async fn policy_service_evaluates_requests_over_unix_socket() {
    let directory = tempdir().expect("create socket directory");
    let socket_path = directory.path().join("policy.sock");
    let listener = bind_uds(&socket_path).expect("bind Unix socket");
    let socket_mode = std::fs::metadata(&socket_path)
        .expect("read socket metadata")
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(socket_mode, 0o600);
    let service = PolicyGrpcService::new(PolicyEngine::new(Config::default()), "test-engine");

    let server = tokio::spawn(async move {
        Server::builder()
            .add_service(PolicyServiceServer::new(service))
            .serve_with_incoming(UnixListenerStream::new(listener))
            .await
    });

    let connector_path = socket_path.clone();
    let channel = Endpoint::try_from("http://[::]:50051")
        .unwrap()
        .connect_with_connector(service_fn(move |_| {
            let path = connector_path.clone();
            async move { UnixStream::connect(path).await.map(TokioIo::new) }
        }))
        .await
        .expect("connect to policy UDS");
    let mut client = PolicyServiceClient::new(channel);

    let capabilities = client
        .get_capabilities(GetCapabilitiesRequest {
            client_protocol: Some(ProtocolVersion { major: 1, minor: 0 }),
        })
        .await
        .expect("get capabilities")
        .into_inner();
    assert_eq!(capabilities.server_protocol.unwrap().major, 1);
    assert!(
        capabilities
            .capabilities
            .contains(&(Capability::RequestPolicy as i32))
    );

    let response = client
        .evaluate_request(support::valid_request())
        .await
        .expect("evaluate request")
        .into_inner();
    assert_eq!(response.result.unwrap().decision, Decision::Allow as i32);

    let mut valid_request = support::valid_request();
    valid_request.context.as_mut().unwrap().request_id = "req-stream".into();
    let event = b"data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n";
    let split = event.len() / 2;
    let requests = vec![
        StreamCheckRequest {
            request_id: "req-stream".into(),
            sequence: 0,
            payload: Some(Payload::Open(Box::new(StreamOpen {
                context: valid_request.context,
                request: valid_request.request,
            }))),
        },
        StreamCheckRequest {
            request_id: "req-stream".into(),
            sequence: 1,
            payload: Some(Payload::Chunk(StreamChunk {
                data: event[..split].to_vec(),
            })),
        },
        StreamCheckRequest {
            request_id: "req-stream".into(),
            sequence: 2,
            payload: Some(Payload::Chunk(StreamChunk {
                data: event[split..].to_vec(),
            })),
        },
        StreamCheckRequest {
            request_id: "req-stream".into(),
            sequence: 3,
            payload: Some(Payload::Chunk(StreamChunk {
                data: b"data: [DONE]\n\n".to_vec(),
            })),
        },
        StreamCheckRequest {
            request_id: "req-stream".into(),
            sequence: 4,
            payload: Some(Payload::Close(StreamClose {})),
        },
    ];
    let mut stream = client
        .check_stream(tokio_stream::iter(requests))
        .await
        .expect("open stream policy")
        .into_inner();
    let mut responses = Vec::new();
    while let Some(response) = stream.message().await.expect("receive stream decision") {
        responses.push(response);
    }
    assert_eq!(responses.len(), 5);
    assert_eq!(responses[0].release_bytes, 0);
    assert_eq!(responses[1].release_bytes, 0);
    assert_eq!(responses[2].release_bytes, event.len() as u64);
    assert_eq!(responses[3].release_bytes, 14);
    assert_eq!(
        responses[4].result.as_ref().unwrap().decision,
        Decision::Allow as i32
    );

    server.abort();
    let _ = tokio::time::timeout(Duration::from_secs(1), server).await;
}
