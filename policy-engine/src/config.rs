#[derive(Clone, Debug)]
pub struct Config {
    pub max_request_bytes: usize,
    pub max_json_depth: usize,
    pub blocked_terms: Vec<String>,
    pub policy_version: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            max_request_bytes: 1_048_576,
            max_json_depth: 64,
            blocked_terms: Vec::new(),
            policy_version: "policy-v1".into(),
        }
    }
}
