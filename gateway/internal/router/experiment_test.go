package router

import (
	"context"
	"testing"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/experiment"
)

func TestControllerRecordsExperimentAssignmentAndRestrictsPool(t *testing.T) {
	experimentController, err := experiment.New(experiment.Config{
		ID: "stage7", Control: experiment.Arm{Policy: "static", BackendPool: []string{"gpu-0"}}, Treatment: experiment.Arm{Policy: "kv-v1", BackendPool: []string{"gpu-1"}},
		Design: experiment.Design{Type: "isolated-pool", Seed: "seed", TreatmentProbability: .5},
	})
	if err != nil {
		t.Fatal(err)
	}
	controller := NewController(ModeEnforced, nil)
	controller.SetExperiment(experimentController)
	decision := controller.Plan(context.Background(), RoutingRequest{RequestID: "request-a", Model: "m", PromptTokens: 100}, []BackendDescriptor{{ID: "gpu-0"}, {ID: "gpu-1"}})
	if decision.ExperimentID != "stage7" || decision.AssignmentUnit != "request" || decision.AssignmentProbability != .5 {
		t.Fatalf("decision=%+v", decision)
	}
	eligible := 0
	for _, candidate := range decision.Candidates {
		if candidate.Eligible {
			eligible++
		}
	}
	if eligible != 1 {
		t.Fatalf("decision=%+v", decision)
	}
}

func TestKVV2VetoesCacheAffinityWhenQueuePenaltyExceedsBenefit(t *testing.T) {
	candidates := []Candidate{
		{BackendID: "hot", Eligible: true, MatchedTokens: 800, QueueDepth: 20, QueuePenaltyMS: 8, RecentPrefillRate: 8000, Score: 100},
		{BackendID: "idle", Eligible: true, QueueDepth: 0, QueuePenaltyMS: 8, Score: 1},
	}
	applyKVV2QueueVeto(candidates)
	sortCandidates(candidates)
	if candidates[0].BackendID != "idle" || candidates[1].Reason != "queue_penalty_exceeds_cache_benefit" {
		t.Fatalf("candidates=%+v", candidates)
	}
}

func TestLifecyclePreventsInactiveTreatmentAssignments(t *testing.T) {
	experimentController, err := experiment.New(experiment.Config{ID: "stage7", Control: experiment.Arm{Policy: "static"}, Treatment: experiment.Arm{Policy: "kv-v2"}, Design: experiment.Design{Type: "switchback", Window: "5m", Seed: "seed"}})
	if err != nil {
		t.Fatal(err)
	}
	lifecycle, err := NewLifecycle(LifecycleConfig{Enabled: true, MinRequests: 10})
	if err != nil {
		t.Fatal(err)
	}
	controller := NewController(ModeEnforced, nil)
	controller.SetLifecycle(lifecycle)
	controller.SetExperiment(experimentController)
	decision := controller.Plan(context.Background(), RoutingRequest{RequestID: "request", Model: "m", PromptTokens: 100}, []BackendDescriptor{{ID: "gpu"}})
	if decision.AssignedPolicy != "static" || decision.ExperimentActive || decision.ExperimentStopReason != "lifecycle_not_enforced" || decision.Enforced {
		t.Fatalf("decision=%+v", decision)
	}
}
