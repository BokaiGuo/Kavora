package router

import (
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"testing"
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

var _ backendstate.Snapshot
