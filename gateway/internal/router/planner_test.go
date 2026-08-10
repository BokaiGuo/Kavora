package router

import (
	"context"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type fixedCacheProvider map[string]CacheEvidence

func (fixedCacheProvider) Name() CacheSource { return CacheSourceKVEvents }
func (provider fixedCacheProvider) Match(_ context.Context, _ CacheMatchRequest, backend CacheBackend) CacheEvidence {
	return provider[backend.ID]
}

func TestPlanFiltersHardConstraintsBeforeScoring(t *testing.T) {
	controller := NewController(ModeEnforced, nil)
	controller.SetCacheProvider(fixedCacheProvider{
		"public":  {Source: CacheSourceKVEvents, Quality: QualityFresh, MatchRatio: 1, MatchedTokens: 100, Confidence: 1},
		"private": {Source: CacheSourceKVEvents, Quality: QualityFresh, MatchRatio: .2, MatchedTokens: 20, Confidence: 1},
	})
	decision := controller.Plan(context.Background(), RoutingRequest{
		RequestID: "req", TenantID: "tenant", CacheKey: "key", PromptTokens: 100,
		Requirements: map[string]string{"trust_zone": "private"},
	}, []BackendDescriptor{
		{ID: "public", Attributes: map[string]string{"trust_zone": "public"}},
		{ID: "private", Attributes: map[string]string{"trust_zone": "private"}},
	})
	if decision.Selected != "private" || decision.Fallback {
		t.Fatalf("decision=%+v", decision)
	}
	if decision.Candidates[0].BackendID != "private" || !decision.Candidates[0].Eligible {
		t.Fatalf("candidates=%+v", decision.Candidates)
	}
	if decision.Candidates[1].Eligible || len(decision.Candidates[1].ExcludedBy) == 0 {
		t.Fatalf("candidates=%+v", decision.Candidates)
	}
}

func TestPlanUsesConfidenceWeightedEvidenceAndSLO(t *testing.T) {
	now := time.Unix(100, 0)
	controller := NewController(ModeEnforced, nil)
	controller.SetNow(func() time.Time { return now })
	controller.SetCacheProvider(fixedCacheProvider{
		"a": {Source: CacheSourceKVEvents, Quality: QualityFresh, MatchRatio: .9, MatchedTokens: 900, Confidence: .1, ObservedAt: now.Add(-5 * time.Second)},
		"b": {Source: CacheSourceKVEvents, Quality: QualityFresh, MatchRatio: .6, MatchedTokens: 600, Confidence: 1, ObservedAt: now},
	})
	controller.states["a"] = plannerState("a", 0, .1, now)
	controller.states["b"] = plannerState("b", 0, .1, now)
	decision := controller.Plan(context.Background(), RoutingRequest{RequestID: "req", PromptTokens: 1000, TTFTSLOMS: 100}, []BackendDescriptor{{ID: "a"}, {ID: "b"}})
	if decision.Selected != "b" {
		t.Fatalf("decision=%+v", decision)
	}
	if decision.Candidates[0].PredictedTTFTMS <= 0 || decision.Candidates[0].SLOViolationProbability <= 0 {
		t.Fatalf("candidate=%+v", decision.Candidates[0])
	}
}

func TestPlanFallsBackWhenAllCandidatesAreIneligible(t *testing.T) {
	controller := NewController(ModeEnforced, nil)
	decision := controller.Plan(context.Background(), RoutingRequest{RequestID: "req", Requirements: map[string]string{"local_only": "true"}}, []BackendDescriptor{{ID: "remote", Attributes: map[string]string{"local_only": "false"}}})
	if !decision.Fallback || decision.Selected != "" || decision.Reason != "no_eligible_backend" {
		t.Fatalf("decision=%+v", decision)
	}
}

func plannerState(id string, queue, pressure float64, observedAt time.Time) backendstate.Snapshot {
	return backendstate.Snapshot{
		SchemaVersion: backendstate.SchemaVersion, BackendID: id, Backend: "vllm", Model: "m", ObservedAtUnixMillis: observedAt.UnixMilli(),
		Signals: map[string]backendstate.Signal{
			"queue_depth": {Value: queue, HasValue: true, Quality: "fresh", Source: "test", ObservedAtUnixMillis: observedAt.UnixMilli()},
			"kv_pressure": {Value: pressure, HasValue: true, Quality: "fresh", Source: "test", ObservedAtUnixMillis: observedAt.UnixMilli()},
		},
	}
}
