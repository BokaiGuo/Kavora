package router

import (
	"encoding/json"
	"errors"
	"math"
)

const PredictorArtifactSchemaVersion = "kavora-ttft-predictor/v1"

type PredictorCoefficients struct {
	InterceptMS         float64 `json:"intercept_ms"`
	UncachedTokenMS     float64 `json:"uncached_token_ms"`
	CachedTokenMS       float64 `json:"cached_token_ms"`
	QueuePenaltyMS      float64 `json:"queue_penalty_ms"`
	KVPressurePenaltyMS float64 `json:"kv_pressure_penalty_ms"`
	SLOScaleMS          float64 `json:"slo_scale_ms"`
}

type PredictorValidation struct {
	MAEMS              float64 `json:"mae_ms"`
	P95AbsoluteErrorMS float64 `json:"p95_absolute_error_ms"`
	Samples            int     `json:"samples"`
	Method             string  `json:"method,omitempty"`
}

type PredictorArtifact struct {
	SchemaVersion    string                `json:"schema_version"`
	PredictorVersion string                `json:"predictor_version"`
	Model            string                `json:"model,omitempty"`
	GPUType          string                `json:"gpu_type,omitempty"`
	BackendEngine    string                `json:"backend_engine,omitempty"`
	BackendVersion   string                `json:"backend_version,omitempty"`
	Coefficients     PredictorCoefficients `json:"coefficients"`
	Validation       PredictorValidation   `json:"validation"`
	ClaimBoundary    string                `json:"claim_boundary,omitempty"`
}

func LoadTTFTPredictor(data []byte) (TTFTPredictor, PredictorArtifact, error) {
	var artifact PredictorArtifact
	if err := json.Unmarshal(data, &artifact); err != nil {
		return TTFTPredictor{}, PredictorArtifact{}, err
	}
	if artifact.SchemaVersion != PredictorArtifactSchemaVersion {
		return TTFTPredictor{}, PredictorArtifact{}, errors.New("unsupported TTFT predictor artifact schema")
	}
	if artifact.PredictorVersion == "" {
		return TTFTPredictor{}, PredictorArtifact{}, errors.New("predictor_version is required")
	}
	if artifact.Model == "" || artifact.GPUType == "" || artifact.BackendEngine == "" || artifact.BackendVersion == "" {
		return TTFTPredictor{}, PredictorArtifact{}, errors.New("predictor runtime dimensions are required")
	}
	coefficients := []float64{
		artifact.Coefficients.InterceptMS,
		artifact.Coefficients.UncachedTokenMS,
		artifact.Coefficients.CachedTokenMS,
		artifact.Coefficients.QueuePenaltyMS,
		artifact.Coefficients.KVPressurePenaltyMS,
	}
	for _, coefficient := range coefficients {
		if coefficient < 0 || math.IsNaN(coefficient) || math.IsInf(coefficient, 0) {
			return TTFTPredictor{}, PredictorArtifact{}, errors.New("TTFT predictor coefficients must be finite and non-negative")
		}
	}
	sloScale := artifact.Coefficients.SLOScaleMS
	if sloScale <= 0 || math.IsNaN(sloScale) || math.IsInf(sloScale, 0) {
		sloScale = 25
	}
	return TTFTPredictor{
		Version:                artifact.PredictorVersion,
		Model:                  artifact.Model,
		GPUType:                artifact.GPUType,
		BackendEngine:          artifact.BackendEngine,
		BackendVersion:         artifact.BackendVersion,
		BaseMS:                 artifact.Coefficients.InterceptMS,
		PromptTokenMS:          artifact.Coefficients.UncachedTokenMS,
		CachedTokenMS:          artifact.Coefficients.CachedTokenMS,
		QueuePenaltyMS:         artifact.Coefficients.QueuePenaltyMS,
		PressurePenalty:        artifact.Coefficients.KVPressurePenaltyMS,
		SLOScaleMS:             sloScale,
		UseObservedPrefillRate: false,
	}, artifact, nil
}
