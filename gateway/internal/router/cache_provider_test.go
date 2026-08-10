package router

import (
	"context"
	"math"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

func TestNoCacheProviderReturnsExplicitMissingEvidence(t *testing.T) {
	evidence := (NoCacheProvider{}).Match(context.Background(), CacheMatchRequest{CacheKey: "key"}, CacheBackend{ID: "gpu-0"})
	if evidence.Source != CacheSourceNone || evidence.Quality != QualityMissing || evidence.Confidence != 0 {
		t.Fatalf("evidence=%+v", evidence)
	}
}

func TestAffinityProviderReturnsPredictedEvidenceOnlyForMappedBackend(t *testing.T) {
	affinity := NewAffinity(8, time.Minute)
	now := time.Unix(100, 0)
	affinity.Put("tenant", "key", "gpu-1", now)
	provider := NewAffinityProvider(affinity, 0.65, func() time.Time { return now })

	match := provider.Match(context.Background(), CacheMatchRequest{TenantID: "tenant", CacheKey: "key", PromptTokens: 100}, CacheBackend{ID: "gpu-1"})
	miss := provider.Match(context.Background(), CacheMatchRequest{TenantID: "tenant", CacheKey: "key", PromptTokens: 100}, CacheBackend{ID: "gpu-0"})

	if match.Source != CacheSourceAffinity || match.MatchRatio != 1 || match.MatchedTokens != 100 || match.Confidence != 0.65 {
		t.Fatalf("match=%+v", match)
	}
	if miss.Quality != QualityMissing || miss.Confidence != 0 {
		t.Fatalf("miss=%+v", miss)
	}
}

func TestShadowIndexProviderUsesResidencyAsPredictedEvidence(t *testing.T) {
	now := time.Unix(100, 0)
	provider := NewShadowIndexProvider(0.55, time.Minute, func() time.Time { return now })
	state := backendstate.Snapshot{
		SchemaVersion: backendstate.SchemaVersion, BackendID: "gpu-0", Backend: "vllm", Model: "m",
		ObservedAtUnixMillis: now.UnixMilli(),
		Signals: map[string]backendstate.Signal{
			"effective_residency_perc": {Value: 0.75, HasValue: true, Quality: "fresh", Source: "test", ObservedAtUnixMillis: now.UnixMilli()},
		},
	}
	evidence := provider.Match(context.Background(), CacheMatchRequest{PromptTokens: 200}, CacheBackend{ID: "gpu-0", State: state})
	if evidence.Source != CacheSourceShadow || evidence.MatchRatio != 0.75 || evidence.MatchedTokens != 150 || evidence.Quality != QualityFresh {
		t.Fatalf("evidence=%+v", evidence)
	}
}

func TestKVEventProviderReturnsExactEvidenceWithAgeDecay(t *testing.T) {
	now := time.Unix(100, 0)
	provider := NewKVEventProvider(16, time.Minute, 0.5, func() time.Time { return now })
	provider.Observe(KVEvent{BackendID: "gpu-1", CacheKey: "key", MatchedTokens: 80, TotalTokens: 100, ObservedAt: now.Add(-2 * time.Second), Quality: QualityFresh})

	evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "key", PromptTokens: 100}, CacheBackend{ID: "gpu-1"})

	if evidence.Source != CacheSourceKVEvents || evidence.MatchRatio != 0.8 || evidence.MatchedTokens != 80 || evidence.Quality != QualityFresh {
		t.Fatalf("evidence=%+v", evidence)
	}
	if math.Abs(evidence.Confidence-math.Exp(-1)) > 1e-9 {
		t.Fatalf("confidence=%f", evidence.Confidence)
	}
}

func TestKVEventProviderMarksExpiredEvidenceStale(t *testing.T) {
	now := time.Unix(100, 0)
	provider := NewKVEventProvider(16, time.Second, 0.1, func() time.Time { return now })
	provider.Observe(KVEvent{BackendID: "gpu-0", CacheKey: "key", MatchedTokens: 10, TotalTokens: 10, ObservedAt: now.Add(-2 * time.Second), Quality: QualityFresh})

	evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "key"}, CacheBackend{ID: "gpu-0"})
	if evidence.Quality != QualityStale || evidence.Confidence != 0 {
		t.Fatalf("evidence=%+v", evidence)
	}
}
