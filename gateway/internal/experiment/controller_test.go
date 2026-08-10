package experiment

import (
	"testing"
	"time"
)

func TestSwitchbackAssignmentIsStableWithinWindowAndMarksGuards(t *testing.T) {
	start := time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC)
	controller, err := New(Config{
		ID: "kv-v2-vs-static", Control: Arm{Policy: "static"}, Treatment: Arm{Policy: "kv-v2"},
		Design: Design{Type: "switchback", Window: "5m", Warmup: "30s", Cooldown: "30s", Seed: "stage7", StartAt: start},
	})
	if err != nil {
		t.Fatal(err)
	}
	first := controller.Assign("request-a", start.Add(10*time.Second))
	second := controller.Assign("request-b", start.Add(4*time.Minute))
	if first.AssignedPolicy != second.AssignedPolicy || first.ExperimentWindow != second.ExperimentWindow {
		t.Fatalf("first=%+v second=%+v", first, second)
	}
	if !first.Warmup || !first.CarryoverGuard || second.Warmup || second.CarryoverGuard {
		t.Fatalf("first=%+v second=%+v", first, second)
	}
}

func TestSwitchbackRejectsNegativeGuardDurations(t *testing.T) {
	_, err := New(Config{
		ID: "invalid-guard", Control: Arm{Policy: "static"}, Treatment: Arm{Policy: "kv-v2"},
		Design: Design{Type: "switchback", Window: "5m", Warmup: "-1s", Seed: "stage7"},
	})
	if err == nil {
		t.Fatal("expected negative warmup to be rejected")
	}
}

func TestIsolatedPoolAssignmentRestrictsBackends(t *testing.T) {
	controller, err := New(Config{
		ID: "isolated", Control: Arm{Policy: "static", BackendPool: []string{"gpu-0"}}, Treatment: Arm{Policy: "kv-v1", BackendPool: []string{"gpu-1"}},
		Design: Design{Type: "isolated-pool", Seed: "pool-seed", TreatmentProbability: .5},
	})
	if err != nil {
		t.Fatal(err)
	}
	assignment := controller.Assign("request-a", time.Now())
	if assignment.AssignmentUnit != "request" || len(assignment.BackendPool) != 1 {
		t.Fatalf("assignment=%+v", assignment)
	}
	if assignment.AssignedPolicy == "static" && assignment.BackendPool[0] != "gpu-0" {
		t.Fatalf("assignment=%+v", assignment)
	}
	if assignment.AssignedPolicy == "kv-v1" && assignment.BackendPool[0] != "gpu-1" {
		t.Fatalf("assignment=%+v", assignment)
	}
}

func TestIsolatedPoolRequiresDisjointNonemptyPools(t *testing.T) {
	for _, testCase := range []struct {
		name      string
		control   []string
		treatment []string
	}{
		{name: "empty", treatment: []string{"gpu-1"}},
		{name: "empty-id", control: []string{""}, treatment: []string{"gpu-1"}},
		{name: "duplicate", control: []string{"gpu-0", "gpu-0"}, treatment: []string{"gpu-1"}},
		{name: "overlap", control: []string{"gpu-0"}, treatment: []string{"gpu-0"}},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := New(Config{
				ID: "invalid-pools", Control: Arm{Policy: "static", BackendPool: testCase.control}, Treatment: Arm{Policy: "kv-v1", BackendPool: testCase.treatment},
				Design: Design{Type: "isolated-pool", Seed: "pool-seed", TreatmentProbability: .5},
			})
			if err == nil {
				t.Fatal("expected invalid isolated pools to be rejected")
			}
		})
	}
}

func TestExperimentStopsAtConfiguredMaximumDuration(t *testing.T) {
	start := time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC)
	controller, err := New(Config{ID: "stopped", Control: Arm{Policy: "static"}, Treatment: Arm{Policy: "kv-v2"}, Design: Design{Type: "switchback", Window: "5m", Seed: "seed", StartAt: start}, Stop: Stop{MaxDuration: "10m"}})
	if err != nil {
		t.Fatal(err)
	}
	assignment := controller.Assign("request", start.Add(11*time.Minute))
	if assignment.ExperimentActive || assignment.StopReason != "max_duration_reached" || assignment.AssignedPolicy != "static" || !assignment.CarryoverGuard {
		t.Fatalf("assignment=%+v", assignment)
	}
}
