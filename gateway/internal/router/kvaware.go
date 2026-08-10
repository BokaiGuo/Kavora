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
	QueueDepth              float64     `json:"queue_depth"`
	KVPressure              float64     `json:"kv_pressure"`
	RecentPrefillRate       float64     `json:"recent_prefill_tokens_per_second"`
	PredictedTTFTMS         float64     `json:"predicted_ttft_ms"`
	SLOViolationProbability float64     `json:"slo_violation_probability"`
	StateConfidence         float64     `json:"state_confidence"`
	Score                   float64     `json:"score"`
	Reason                  string      `json:"reason"`
}

type Decision struct {
	RequestID      string            `json:"request_id"`
	TenantID       string            `json:"tenant_id"`
	CacheKey       string            `json:"cache_key,omitempty"`
	PolicyVersion  string            `json:"policy_version"`
	Mode           string            `json:"mode"`
	Stage          string            `json:"stage,omitempty"`
	Enforced       bool              `json:"enforced"`
	CanaryFraction float64           `json:"canary_fraction"`
	Requirements   map[string]string `json:"requirements,omitempty"`
	Selected       string            `json:"selected"`
	ActualSelected string            `json:"actual_selected,omitempty"`
	Fallback       bool              `json:"fallback"`
	Reason         string            `json:"reason"`
	Reasons        []string          `json:"reasons,omitempty"`
	Candidates     []Candidate       `json:"candidates"`
	OccurredAt     time.Time         `json:"occurred_at"`
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
