package router

import (
	"sort"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type Candidate struct {
	BackendID               string      `json:"backend_id"`
	Eligible                bool        `json:"eligible"`
	ExcludedBy              []string    `json:"excluded_by,omitempty"`
	PrefixMatch             float64     `json:"prefix_match"`
	MatchedTokens           int         `json:"matched_tokens"`
	CacheSource             CacheSource `json:"cache_source"`
	CacheQuality            Quality     `json:"cache_quality"`
	CacheConfidence         float64     `json:"cache_confidence"`
	EvidenceQuality         string      `json:"evidence_quality"`
	QueueDepth              float64     `json:"queue_depth"`
	QueuePenaltyMS          float64     `json:"queue_penalty_ms"`
	KVPressure              float64     `json:"kv_pressure"`
	RecentPrefillRate       float64     `json:"recent_prefill_tokens_per_second"`
	PredictorVersion        string      `json:"predictor_version"`
	PredictedTTFTMS         float64     `json:"predicted_ttft_ms"`
	SLOViolationProbability float64     `json:"slo_violation_probability"`
	StateConfidence         float64     `json:"state_confidence"`
	Score                   float64     `json:"score"`
	Reason                  string      `json:"reason"`
}

type Decision struct {
	RequestID             string            `json:"request_id"`
	TenantID              string            `json:"tenant_id"`
	CacheKey              string            `json:"cache_key,omitempty"`
	HashAlignment         string            `json:"hash_alignment,omitempty"`
	ExternalCacheKeyCount int               `json:"external_cache_key_count,omitempty"`
	ExperimentID          string            `json:"experiment_id,omitempty"`
	AssignedPolicy        string            `json:"assigned_policy,omitempty"`
	AssignmentUnit        string            `json:"assignment_unit,omitempty"`
	AssignmentProbability float64           `json:"assignment_probability,omitempty"`
	AssignmentSeed        string            `json:"assignment_seed,omitempty"`
	ExperimentWindow      string            `json:"experiment_window,omitempty"`
	Warmup                bool              `json:"warmup,omitempty"`
	CarryoverGuard        bool              `json:"carryover_guard,omitempty"`
	ExperimentActive      bool              `json:"experiment_active,omitempty"`
	ExperimentStopReason  string            `json:"experiment_stop_reason,omitempty"`
	PolicyVersion         string            `json:"policy_version"`
	PredictorVersion      string            `json:"predictor_version"`
	Mode                  string            `json:"mode"`
	Stage                 string            `json:"stage,omitempty"`
	Enforced              bool              `json:"enforced"`
	CanaryFraction        float64           `json:"canary_fraction"`
	Requirements          map[string]string `json:"requirements,omitempty"`
	Selected              string            `json:"selected"`
	ActualSelected        string            `json:"actual_selected,omitempty"`
	Fallback              bool              `json:"fallback"`
	Reason                string            `json:"reason"`
	Reasons               []string          `json:"reasons,omitempty"`
	Candidates            []Candidate       `json:"candidates"`
	OccurredAt            time.Time         `json:"occurred_at"`
	Outcome               *DecisionOutcome  `json:"outcome,omitempty"`
	PredictionError       *PredictionError  `json:"prediction_error,omitempty"`
}

type DecisionOutcome struct {
	RequestID             string    `json:"request_id"`
	ActualBackend         string    `json:"actual_backend"`
	TTFTMS                float64   `json:"ttft_ms"`
	E2EMS                 float64   `json:"e2e_ms"`
	Success               bool      `json:"success"`
	StatusCode            int       `json:"status_code"`
	PromptTokens          int       `json:"prompt_tokens"`
	OutputTokens          int       `json:"output_tokens"`
	Model                 string    `json:"model,omitempty"`
	GPUType               string    `json:"gpu_type,omitempty"`
	BackendEngine         string    `json:"backend_engine,omitempty"`
	BackendVersion        string    `json:"backend_version,omitempty"`
	ObservedCacheHitRatio *float64  `json:"observed_cache_hit_ratio,omitempty"`
	ObservedMatchedTokens *int      `json:"observed_matched_tokens,omitempty"`
	CompletedAt           time.Time `json:"completed_at"`
}

type PredictionError struct {
	PredictorVersion       string   `json:"predictor_version"`
	PredictedTTFTMS        float64  `json:"predicted_ttft_ms"`
	ActualTTFTMS           float64  `json:"actual_ttft_ms"`
	TTFTSignedMS           float64  `json:"ttft_signed_error_ms"`
	TTFTAbsoluteMS         float64  `json:"ttft_absolute_error_ms"`
	PredictedCacheHitRatio *float64 `json:"predicted_cache_hit_ratio,omitempty"`
	ActualCacheHitRatio    *float64 `json:"actual_cache_hit_ratio,omitempty"`
	CacheHitAbsoluteError  *float64 `json:"cache_hit_absolute_error,omitempty"`
	EvidenceQuality        string   `json:"evidence_quality"`
}

type SLOCalibrationBucket struct {
	Bucket                    string  `json:"bucket"`
	Samples                   int     `json:"samples"`
	PredictedProbability      float64 `json:"predicted_probability"`
	ActualViolationRate       float64 `json:"actual_violation_rate"`
	PredictedProbabilityTotal float64 `json:"-"`
	ActualViolations          int     `json:"-"`
}

type EvidenceQualityError struct {
	EvidenceQuality    string  `json:"evidence_quality"`
	Samples            int     `json:"samples"`
	MAEMS              float64 `json:"mae_ms"`
	CacheSamples       int     `json:"cache_samples"`
	CacheHitMAE        float64 `json:"cache_hit_mae"`
	AbsoluteErrorTotal float64 `json:"-"`
	CacheErrorTotal    float64 `json:"-"`
}

type PredictionQuality struct {
	Samples            int                    `json:"samples"`
	MAEMS              float64                `json:"mae_ms"`
	P95AbsoluteErrorMS float64                `json:"p95_absolute_error_ms"`
	MeanSignedErrorMS  float64                `json:"mean_signed_error_ms"`
	Status             string                 `json:"status"`
	SLOCalibration     []SLOCalibrationBucket `json:"slo_calibration"`
	Evidence           []EvidenceQualityError `json:"evidence_quality"`
}

type Evaluator struct {
	Now func() time.Time
}

func (e Evaluator) Shadow(requestID, tenantID string, states map[string]backendstate.Snapshot) Decision {
	now := time.Now
	if e.Now != nil {
		now = e.Now
	}
	decision := Decision{RequestID: requestID, TenantID: tenantID, Mode: "shadow", Fallback: true, Reason: "no_usable_backend_state", OccurredAt: now().UTC()}
	for id, snapshot := range states {
		score, ok := backendstate.Value(snapshot, "cold_free_perc")
		if !ok || snapshot.Signals["cold_free_perc"].Quality == "stale" {
			decision.Candidates = append(decision.Candidates, Candidate{BackendID: id, Reason: "missing_or_stale_cold_free"})
			continue
		}
		decision.Candidates = append(decision.Candidates, Candidate{BackendID: id, Eligible: true, Score: 1 - score, Reason: "cold_free_inverse"})
	}
	sortCandidates(decision.Candidates)
	if len(decision.Candidates) > 0 && decision.Candidates[0].Reason == "cold_free_inverse" {
		decision.Selected = decision.Candidates[0].BackendID
		decision.Fallback = false
		decision.Reason = "highest_kv_residency_score"
	}
	return decision
}

func (e Evaluator) LoadAware(requestID, tenantID string, states map[string]backendstate.Snapshot) Decision {
	now := time.Now
	if e.Now != nil {
		now = e.Now
	}
	decision := Decision{RequestID: requestID, TenantID: tenantID, Mode: string(ModeLoadAware), Fallback: true, Reason: "no_usable_queue_state", OccurredAt: now().UTC()}
	for id, snapshot := range states {
		queueDepth, ok := backendstate.Value(snapshot, "queue_depth")
		if !ok || snapshot.Signals["queue_depth"].Quality == "stale" {
			decision.Candidates = append(decision.Candidates, Candidate{BackendID: id, Reason: "missing_or_stale_queue_depth"})
			continue
		}
		decision.Candidates = append(decision.Candidates, Candidate{BackendID: id, Eligible: true, Score: -queueDepth, Reason: "queue_depth_inverse"})
	}
	sortCandidates(decision.Candidates)
	if len(decision.Candidates) > 0 && decision.Candidates[0].Reason == "queue_depth_inverse" {
		decision.Selected = decision.Candidates[0].BackendID
		decision.Fallback = false
		decision.Reason = "lowest_queue_depth"
	}
	return decision
}

func sortCandidates(candidates []Candidate) {
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Eligible != candidates[j].Eligible {
			return candidates[i].Eligible
		}
		if candidates[i].Score == candidates[j].Score {
			return candidates[i].BackendID < candidates[j].BackendID
		}
		return candidates[i].Score > candidates[j].Score
	})
}
