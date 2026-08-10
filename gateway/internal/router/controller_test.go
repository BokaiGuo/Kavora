package router

import (
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"testing"
	"time"
)

func TestControllerFallsBackWhenStateMissing(t *testing.T) {
	c := NewController(ModeEnforced, nil)
	if got := c.PreferredIDs("r", "t", "k"); got != nil {
		t.Fatalf("got %v", got)
	}
}
func TestControllerPrefersHealthyState(t *testing.T) {
	c := NewController(ModeEnforced, nil)
	if err := c.SetState(state("b", .1, "fresh")); err != nil {
		t.Fatal(err)
	}
	if err := c.SetState(state("a", .8, "fresh")); err != nil {
		t.Fatal(err)
	}
	got := c.PreferredIDs("r", "t", "k")
	if len(got) != 1 || got[0] != "b" {
		t.Fatalf("got %v", got)
	}
}

func TestControllerShadowExplainsWithoutEnforcing(t *testing.T) {
	c := NewController(ModeShadow, nil)
	if err := c.SetState(state("b", .1, "fresh")); err != nil {
		t.Fatal(err)
	}
	decision := c.Decide("r", "t", "k")
	if decision.Selected != "b" || decision.Fallback {
		t.Fatalf("decision=%+v", decision)
	}
	if got := c.PreferredIDs("r", "t", "k"); got != nil {
		t.Fatalf("shadow preferred IDs = %v, want no enforced ordering", got)
	}
}

func TestControllerEnforcesLoadAwareMode(t *testing.T) {
	c := NewController(ModeLoadAware, nil)
	if err := c.SetState(stateWithQueue("a", 4, "fresh")); err != nil {
		t.Fatal(err)
	}
	if err := c.SetState(stateWithQueue("b", 1, "fresh")); err != nil {
		t.Fatal(err)
	}
	decision := c.Decide("r", "t", "k")
	if decision.Mode != string(ModeLoadAware) || decision.Selected != "b" {
		t.Fatalf("decision=%+v", decision)
	}
	if got := c.PreferredIDs("r", "t", "k"); len(got) != 1 || got[0] != "b" {
		t.Fatalf("preferred IDs=%v", got)
	}
}

func TestControllerFallsBackWhenStateExceedsMaxAge(t *testing.T) {
	c := NewController(ModeLoadAware, nil)
	c.SetMaxStateAge(time.Second)
	snapshot := stateWithQueue("a", 0, "fresh")
	snapshot.ObservedAtUnixMillis = time.Now().Add(-2 * time.Second).UnixMilli()
	if err := c.SetState(snapshot); err != nil {
		t.Fatal(err)
	}
	decision := c.Decide("r", "t", "k")
	if !decision.Fallback || decision.Selected != "" {
		t.Fatalf("decision=%+v", decision)
	}
}

var _ backendstate.Snapshot
