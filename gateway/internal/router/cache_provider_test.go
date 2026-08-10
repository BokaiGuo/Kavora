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

func TestKVEventProviderHandlesNativeLifecycleAndGeneration(t *testing.T) {
	provider := NewKVEventProvider(16, time.Minute, .1, time.Now)
	provider.Observe(KVEvent{Operation: "store", BackendID: "gpu-0", CacheKey: "block-a", MatchedTokens: 16, TotalTokens: 16, Sequence: 1, Generation: "gen-1", ObservedAt: time.Now(), Quality: QualityFresh})
	provider.Observe(KVEvent{Operation: "store", BackendID: "gpu-0", CacheKey: "block-a", MatchedTokens: 1, TotalTokens: 16, Sequence: 1, Generation: "gen-1", ObservedAt: time.Now(), Quality: QualityFresh})
	evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "block-a", PromptTokens: 16}, CacheBackend{ID: "gpu-0"})
	if evidence.MatchedTokens != 16 || evidence.EvidenceQuality != "strict" {
		t.Fatalf("evidence=%+v", evidence)
	}
	provider.Observe(KVEvent{Operation: "remove", BackendID: "gpu-0", CacheKey: "block-a", Sequence: 2, Generation: "gen-1", ObservedAt: time.Now()})
	if evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "block-a", PromptTokens: 16}, CacheBackend{ID: "gpu-0"}); evidence.Quality != QualityMissing {
		t.Fatalf("evidence=%+v", evidence)
	}
	provider.Observe(KVEvent{Operation: "store", BackendID: "gpu-0", CacheKey: "block-b", MatchedTokens: 16, TotalTokens: 16, Sequence: 1, Generation: "gen-2", ObservedAt: time.Now(), Quality: QualityFresh})
	if evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "block-a", PromptTokens: 16}, CacheBackend{ID: "gpu-0"}); evidence.Quality != QualityMissing {
		t.Fatalf("old generation evidence=%+v", evidence)
	}
	provider.Observe(KVEvent{Operation: "clear", BackendID: "gpu-0", Sequence: 2, Generation: "gen-2", ObservedAt: time.Now()})
	if evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "block-b", PromptTokens: 16}, CacheBackend{ID: "gpu-0"}); evidence.Quality != QualityMissing {
		t.Fatalf("cleared evidence=%+v", evidence)
	}
}

func TestKVEventProviderAcceptsDistinctEventsFromSameNativeBatch(t *testing.T) {
	provider := NewKVEventProvider(16, time.Minute, .1, time.Now)
	provider.Observe(KVEvent{Operation: "store", BackendID: "gpu-0", CacheKey: "block-a", MatchedTokens: 16, TotalTokens: 16, Sequence: 4, HasSequence: true, Generation: "gen", EngineEventID: "gen:4:1", ObservedAt: time.Now()})
	provider.Observe(KVEvent{Operation: "store", BackendID: "gpu-0", CacheKey: "block-b", MatchedTokens: 16, TotalTokens: 16, Sequence: 4, HasSequence: true, Generation: "gen", EngineEventID: "gen:4:2", ObservedAt: time.Now()})
	for _, key := range []string{"block-a", "block-b"} {
		if evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: key, PromptTokens: 16}, CacheBackend{ID: "gpu-0"}); evidence.MatchedTokens != 16 {
			t.Fatalf("key=%s evidence=%+v", key, evidence)
		}
	}
}

func TestKVEventProviderScopesNativeEventIDsByBackend(t *testing.T) {
	provider := NewKVEventProvider(16, time.Minute, .1, time.Now)
	for _, backendID := range []string{"gpu-0", "gpu-1"} {
		provider.Observe(KVEvent{Operation: "store", BackendID: backendID, CacheKey: "block", MatchedTokens: 16, TotalTokens: 16, Sequence: 1, HasSequence: true, Generation: "gen", EngineEventID: "gen:1:1", ObservedAt: time.Now()})
	}
	for _, backendID := range []string{"gpu-0", "gpu-1"} {
		if evidence := provider.Match(context.Background(), CacheMatchRequest{CacheKey: "block", PromptTokens: 16}, CacheBackend{ID: backendID}); evidence.MatchedTokens != 16 {
			t.Fatalf("backend=%s evidence=%+v", backendID, evidence)
		}
	}
}
