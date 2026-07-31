package router

import (
	"sort"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type Candidate struct {
	BackendID string
	Score     float64
	Reason    string
}

type Decision struct {
	RequestID  string
	TenantID   string
	Selected   string
	Mode       string
	Fallback   bool
	Reason     string
	Candidates []Candidate
	OccurredAt time.Time
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
		decision.Candidates = append(decision.Candidates, Candidate{BackendID: id, Score: 1 - score, Reason: "cold_free_inverse"})
	}
	sort.Slice(decision.Candidates, func(i, j int) bool { return decision.Candidates[i].Score > decision.Candidates[j].Score })
	if len(decision.Candidates) > 0 && decision.Candidates[0].Reason == "cold_free_inverse" {
		decision.Selected = decision.Candidates[0].BackendID
		decision.Fallback = false
		decision.Reason = "highest_kv_residency_score"
	}
	return decision
}
