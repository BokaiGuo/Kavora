package router

import "testing"

func TestLifecyclePromotesAndRollsBack(t *testing.T) {
	lifecycle, err := NewLifecycle(LifecycleConfig{Enabled: true, MinRequests: 10, CanarySteps: []float64{.05, .25, .5, 1}, Gates: LifecycleGates{MaxP95RegressionPercent: 5, MaxErrorDelta: .01, MaxFallbackRate: .02, MaxSLOViolationRate: .05}})
	if err != nil {
		t.Fatal(err)
	}
	good := LifecycleObservation{Requests: 10, StateHealthy: true, PolicyHealthy: true}
	if snapshot := lifecycle.Observe(good); snapshot.Stage != StageCanary || snapshot.CanaryFraction != .05 {
		t.Fatalf("snapshot=%+v", snapshot)
	}
	if snapshot := lifecycle.Observe(good); snapshot.CanaryFraction != .25 {
		t.Fatalf("snapshot=%+v", snapshot)
	}
	bad := good
	bad.FallbackRate = .5
	if snapshot := lifecycle.Observe(bad); snapshot.Stage != StageStatic || snapshot.LastReason != "quality_gate_failed" {
		t.Fatalf("snapshot=%+v", snapshot)
	}
}

func TestLifecycleUnhealthySignalAlwaysRollsBack(t *testing.T) {
	lifecycle, _ := NewLifecycle(LifecycleConfig{Enabled: true})
	snapshot := lifecycle.Observe(LifecycleObservation{Requests: 100, StateHealthy: false, PolicyHealthy: true})
	if snapshot.Stage != StageStatic {
		t.Fatalf("snapshot=%+v", snapshot)
	}
}
