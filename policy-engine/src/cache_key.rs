use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

#[derive(Serialize)]
pub(crate) struct CanonicalMessage<'a> {
    pub role: &'a str,
    pub content: &'a Value,
    pub name: Option<&'a str>,
}

#[derive(Serialize)]
pub(crate) struct CanonicalRequest<'a> {
    pub model: &'a str,
    pub messages: &'a [CanonicalMessage<'a>],
    pub tools: &'a Value,
    pub generation_parameters: &'a Value,
}

pub(crate) fn calculate(request: &CanonicalRequest<'_>) -> Vec<u8> {
    let encoded = serde_json::to_vec(request).expect("canonical request serialization cannot fail");
    Sha256::digest(encoded).to_vec()
}
