package workloadreplay

import (
	"strings"
	"testing"
)

func TestReadTraceRejectsPromptContent(t *testing.T) {
	_, err := ReadTrace(strings.NewReader(`{"prompt_tokens":100,"output_tokens":10,"shared_prefix_hash":"x","shared_prefix_tokens":80,"tenant_class":"research","arrival_delta_ms":0,"streaming":true,"model":"m","prompt":"secret"}` + "\n"))
	if err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("err=%v", err)
	}
}

func TestCandidateReplayImprovesRepeatedPrefixAndRequiresApproval(t *testing.T) {
	trace := []Signature{
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "a", SharedPrefixTokens: 800, TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "b", SharedPrefixTokens: 800, TenantClass: "research", ArrivalDeltaMS: 5, Model: "m"},
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "a", SharedPrefixTokens: 800, TenantClass: "research", ArrivalDeltaMS: 5, Model: "m"},
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "b", SharedPrefixTokens: 800, TenantClass: "research", ArrivalDeltaMS: 5, Model: "m"},
	}
	report, err := Compare(trace, Config{Backends: 3, MinHitRatio: .5, MaxConcurrency: 8, EvidenceQuality: "strict", TTFTSLOMS: 500, PrefillTokensPerSecond: 10000, DecodeTokensPerSecond: 100})
	if err != nil {
		t.Fatal(err)
	}
	if report.Candidate.P95TTFTMS >= report.Baseline.P95TTFTMS {
		t.Fatalf("report=%+v", report)
	}
	if report.Candidate.CacheReuseRatio <= report.Baseline.CacheReuseRatio {
		t.Fatalf("report=%+v", report)
	}
	if report.Recommendation != "SAFE_FOR_CANARY" || report.ApprovalStatus != "human_approval_required" {
		t.Fatalf("report=%+v", report)
	}
}

func TestConcurrencyAdmissionPreservesSourceArrivalClock(t *testing.T) {
	trace := []Signature{
		{PromptTokens: 1000, SharedPrefixHash: "a", TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, SharedPrefixHash: "b", TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, SharedPrefixHash: "c", TenantClass: "research", ArrivalDeltaMS: 100, Model: "m"},
		{PromptTokens: 1000, SharedPrefixHash: "d", TenantClass: "research", ArrivalDeltaMS: 100, Model: "m"},
	}
	metrics := simulate(trace, Config{
		Backends:               2,
		MaxConcurrency:         2,
		EvidenceQuality:        "strict",
		TTFTSLOMS:              5000,
		PrefillTokensPerSecond: 1000,
		DecodeTokensPerSecond:  100,
	}, false)
	if metrics.ThroughputReqS < 1.97 || metrics.ThroughputReqS > 1.99 {
		t.Fatalf("throughput=%f", metrics.ThroughputReqS)
	}
}

func TestCandidateDoesNotCreateAffinityForEmptyPrefixHash(t *testing.T) {
	trace := []Signature{
		{PromptTokens: 1000, TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, TenantClass: "research", Model: "m"},
	}
	metrics := simulate(trace, Config{
		Backends:               2,
		MinHitRatio:            0,
		MaxConcurrency:         4,
		EvidenceQuality:        "strict",
		TTFTSLOMS:              5000,
		PrefillTokensPerSecond: 1000,
		DecodeTokensPerSecond:  100,
	}, true)
	if metrics.CacheReuseRatio != 0 || metrics.BackendImbalance != 0 {
		t.Fatalf("metrics=%+v", metrics)
	}
}

func TestPolicyLaboratoryEvaluatesMultiplePolicies(t *testing.T) {
	trace := []Signature{
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "a", SharedPrefixTokens: 800, TenantClass: "research", Model: "m"},
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "b", SharedPrefixTokens: 800, TenantClass: "research", ArrivalDeltaMS: 5, Model: "m"},
		{PromptTokens: 1000, OutputTokens: 32, SharedPrefixHash: "a", SharedPrefixTokens: 800, TenantClass: "research", ArrivalDeltaMS: 5, Model: "m"},
	}
	report, err := EvaluatePolicies(trace, Config{Backends: 3, MinHitRatio: .4, MaxConcurrency: 8, EvidenceQuality: "strict"}, []string{"static", "load-aware", "kv-v1", "kv-v2"})
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Policies) != 4 || report.Policies[0].Policy != "static" || report.Policies[3].Policy != "kv-v2" {
		t.Fatalf("report=%+v", report)
	}
	if report.ClaimBoundary == "" {
		t.Fatal("policy laboratory must retain the simulation claim boundary")
	}
}

func TestPolicyReplayDoesNotReusePrefixesAcrossModels(t *testing.T) {
	trace := []Signature{
		{PromptTokens: 1000, SharedPrefixHash: "same", SharedPrefixTokens: 800, TenantClass: "research", Model: "model-a"},
		{PromptTokens: 1000, SharedPrefixHash: "same", SharedPrefixTokens: 800, TenantClass: "research", Model: "model-b"},
	}
	metrics := simulatePolicy(trace, normalize(Config{Backends: 1, EvidenceQuality: "strict"}), "kv-v1")
	if metrics.CacheReuseRatio != 0 {
		t.Fatalf("metrics=%+v", metrics)
	}
}
