package router

import (
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"testing"
	"time"
)

func state(id string, cold float64, quality string) backendstate.Snapshot {
	return backendstate.Snapshot{SchemaVersion: backendstate.SchemaVersion, BackendID: id, Backend: "vllm", Model: "m", ObservedAtUnixMillis: 1, Signals: map[string]backendstate.Signal{
		"cold_free_perc": {Value: cold, HasValue: quality != "missing", Quality: quality, Source: "test", ObservedAtUnixMillis: 1},
	}}
}

func stateWithQueue(id string, queue float64, quality string) backendstate.Snapshot {
	snapshot := state(id, .5, "fresh")
	snapshot.Signals["queue_depth"] = backendstate.Signal{Value: queue, HasValue: quality != "missing", Quality: quality, Source: "test", ObservedAtUnixMillis: 1}
	return snapshot
}
func TestShadowFallsBackOnMissing(t *testing.T) {
	d := (Evaluator{}).Shadow("r", "t", map[string]backendstate.Snapshot{"a": state("a", 0, "missing")})
	if !d.Fallback || d.Selected != "" {
		t.Fatalf("decision=%+v", d)
	}
}
func TestShadowSelectsHighestResidency(t *testing.T) {
	d := (Evaluator{}).Shadow("r", "t", map[string]backendstate.Snapshot{"a": state("a", .7, "fresh"), "b": state("b", .2, "fresh")})
	if d.Selected != "b" || d.Fallback {
		t.Fatalf("decision=%+v", d)
	}
}

func TestLoadAwareSelectsLowestFreshQueue(t *testing.T) {
	d := (Evaluator{}).LoadAware("r", "t", map[string]backendstate.Snapshot{
		"a": stateWithQueue("a", 3, "fresh"),
		"b": stateWithQueue("b", 1, "fresh"),
	})
	if d.Selected != "b" || d.Fallback || d.Reason != "lowest_queue_depth" {
		t.Fatalf("decision=%+v", d)
	}
}

func TestLoadAwareFallsBackWhenQueueStateIsStale(t *testing.T) {
	d := (Evaluator{}).LoadAware("r", "t", map[string]backendstate.Snapshot{"a": stateWithQueue("a", 0, "stale")})
	if !d.Fallback || d.Selected != "" {
		t.Fatalf("decision=%+v", d)
	}
}
func TestAffinityIsTenantIsolatedAndBounded(t *testing.T) {
	a := NewAffinity(1, time.Minute)
	now := time.Unix(0, 0)
	a.Put("t1", "k", "b1", now)
	a.Put("t2", "k", "b2", now)
	if a.Len() != 1 {
		t.Fatalf("len=%d", a.Len())
	}
	if _, ok := a.Get("t1", "k", now); ok {
		t.Fatal("evicted entry returned")
	}
}
func TestGuardrailsCooldown(t *testing.T) {
	g := NewGuardrails()
	now := time.Unix(0, 0)
	g.changed = now
	if g.SetMode(ModeShadow, now) {
		t.Fatal("transition during cooldown should be rejected")
	}
	g.changed = time.Unix(-61, 0)
	if !g.SetMode(ModeShadow, now) {
		t.Fatal("expected transition")
	}
}
