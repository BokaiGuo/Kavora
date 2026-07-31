use std::{
    env,
    error::Error,
    fs,
    os::unix::fs::{FileTypeExt, PermissionsExt},
    path::{Path, PathBuf},
};

use kavora_policy::{
    config::Config,
    policy::PolicyEngine,
    policy_v1::policy_service_server::PolicyServiceServer,
    service::{PolicyGrpcService, bind_uds},
};
use tokio_stream::wrappers::UnixListenerStream;
use tonic::transport::Server;

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let (socket_path, uses_default_path) = socket_path()?;
    let parent = socket_path
        .parent()
        .ok_or("policy socket path must have a parent directory")?;
    fs::create_dir_all(parent)?;
    if uses_default_path {
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }

    let config = Config {
        blocked_terms: env::var("KAVORA_POLICY_BLOCKED_TERMS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|term| !term.is_empty())
            .map(str::to_owned)
            .collect(),
        ..Config::default()
    };
    let max_decoding_message_size = config.max_request_bytes.saturating_add(64 * 1024);
    let listener = bind_uds(&socket_path)?;
    let service = PolicyGrpcService::new(PolicyEngine::new(config), VERSION);

    eprintln!(
        "kavora-policy {VERSION} listening on {}",
        socket_path.display()
    );
    let result = Server::builder()
        .add_service(
            PolicyServiceServer::new(service).max_decoding_message_size(max_decoding_message_size),
        )
        .serve_with_incoming_shutdown(UnixListenerStream::new(listener), shutdown_signal())
        .await;

    remove_socket_if_present(&socket_path);
    result?;
    Ok(())
}

fn socket_path() -> Result<(PathBuf, bool), Box<dyn Error>> {
    if let Some(path) = env::var_os("KAVORA_POLICY_SOCKET") {
        return Ok((PathBuf::from(path), false));
    }

    let runtime_base = env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".local").join("run")))
        .ok_or("XDG_RUNTIME_DIR or HOME is required for the default policy socket")?;
    Ok((runtime_base.join("kavora").join("policy.sock"), true))
}

async fn shutdown_signal() {
    if let Err(error) = tokio::signal::ctrl_c().await {
        eprintln!("failed to install shutdown signal handler: {error}");
    }
}

fn remove_socket_if_present(path: &Path) {
    if path
        .symlink_metadata()
        .is_ok_and(|metadata| metadata.file_type().is_socket())
    {
        let _ = fs::remove_file(path);
    }
}
