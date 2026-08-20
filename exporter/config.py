from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KVCACHE_", extra="ignore")

    backend_metrics_url: str = "http://localhost:8000/metrics"
    backend_type: str = "vllm"
    exporter_host: str = "0.0.0.0"
    exporter_port: int = 9108
    model_name: str = "unknown"
    instance_name: str = "local"
    backend_id: str = ""
    model_group: str = ""
    poll_interval_s: float = 2.0
    scrape_stale_after_s: float = 120.0
    scrape_failures_not_ready: int = 3
    scrape_error_max_len: int = 256
    tokens_per_block: int = 16
    state_dir: str = "results/kavora-state"
