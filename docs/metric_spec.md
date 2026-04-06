# 指标说明（Metric Spec）

本文档对应当前仓库实现（`exporter/prometheus_parse.py`、`exporter/adapters/*`、`benchmark/collect.py`）。

---

## 1) Prometheus 聚合策略（label-aware）

后端 exposition 常包含 labels（如 `model_name`、`engine`、`gpu`）。当前实现将其聚合为 `dict[str, float]` 的规则：

1. 解析：`prometheus_client.parser.text_string_to_metric_families`。  
2. 聚合：对 `counter / gauge / unknown` 按同名 `sample.name` 求和。  
3. 跳过：`histogram / summary` 不进入扁平 map。  
4. 过滤：支持 `METRIC_LABEL_FILTER`（`k=v,k2=v2`）或函数参数 `label_filter`。  

回退路径：当标准解析失败时，走按行宽松解析（此时 label 过滤不保证生效）。

---

## 2) vLLM 前缀缓存计数兼容

`exporter/adapters/vllm.py` 兼容以下命名：

- 新版优先：`vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`
- 旧版回退：`vllm:prefix_cache_hits` / `vllm:prefix_cache_queries`

派生命中率：

- `kvcache_kv_cache_hit_ratio = prefix_hits / prefix_queries`（queries > 0）

---

## 3) Exporter 对外关键指标

- 业务派生：
  - `kvcache_kv_cache_hit_ratio`
  - `kvcache_kv_hidden_reuse_ready_perc`
  - `kvcache_kv_effective_residency_perc`
  - `kvcache_kv_cold_free_perc`
- 自监控：
  - `kvcache_exporter_scrape_last_success_timestamp_seconds`
  - `kvcache_exporter_scrape_consecutive_failures`
  - `kvcache_exporter_scrape_failures_total`

---

## 4) 当前语义边界

当前仓库未默认打 `vllm_obs` block 语义补丁时：

- `kvcache_kv_hidden_reuse_ready_perc` 可能长期接近 0（缺少可复用块真值输入）
- `kvcache_kv_cache_hit_ratio` 仍可从 vLLM 前缀计数得出并用于阈值扫描
