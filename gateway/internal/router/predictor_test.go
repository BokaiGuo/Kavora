package router

import (
	"math"
	"testing"
)

func TestLoadTTFTPredictorArtifactPreservesExplainableCoefficients(t *testing.T) {
	predictor, artifact, err := LoadTTFTPredictor([]byte(`{
  "schema_version":"kavora-ttft-predictor/v1",
  "predictor_version":"qwen3-8b-4090-v3",
  "model":"qwen3-8b",
  "gpu_type":"RTX-4090",
  "backend_engine":"vllm",
  "backend_version":"0.10",
  "coefficients":{
    "intercept_ms":14.2,
    "uncached_token_ms":0.091,
    "cached_token_ms":0.013,
    "queue_penalty_ms":7.4,
    "kv_pressure_penalty_ms":38.1,
    "slo_scale_ms":25
  },
  "validation":{"mae_ms":18.7,"p95_absolute_error_ms":47.1,"samples":18420}
}`))
	if err != nil {
		t.Fatal(err)
	}
	if predictor.Version != "qwen3-8b-4090-v3" || predictor.UseObservedPrefillRate {
		t.Fatalf("predictor=%+v", predictor)
	}
	if !predictor.Matches("qwen3-8b", map[string]string{"gpu_type": "RTX-4090", "engine": "vllm", "engine_version": "0.10"}) || predictor.Matches("other", map[string]string{"gpu_type": "RTX-4090", "engine": "vllm", "engine_version": "0.10"}) {
		t.Fatalf("predictor scope=%+v", predictor)
	}
	if got := predictor.Predict(1000, 400, 2, .5, 9000); math.Abs(got-107.85) > 1e-9 {
		t.Fatalf("prediction=%v", got)
	}
	if artifact.Validation.Samples != 18420 {
		t.Fatalf("artifact=%+v", artifact)
	}
}

func TestLoadTTFTPredictorRejectsInvalidArtifact(t *testing.T) {
	_, _, err := LoadTTFTPredictor([]byte(`{"schema_version":"kavora-ttft-predictor/v1","predictor_version":"bad","model":"m","gpu_type":"g","backend_engine":"e","backend_version":"v","coefficients":{"uncached_token_ms":-1}}`))
	if err == nil {
		t.Fatal("expected invalid coefficient error")
	}
}
