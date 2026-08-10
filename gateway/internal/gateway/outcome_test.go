package gateway_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
)

func TestGatewayAttachesRealizedOutcomeToRoutingDecision(t *testing.T) {
	backendServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.Header().Set("X-Kavora-Cache-Hit-Ratio", "0.75")
		writer.Header().Set("X-Kavora-Matched-Tokens", "750")
		writer.Header().Set("X-Kavora-TTFT-MS", "0.05")
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"choices": []any{map[string]any{"message": map[string]string{"content": "ok"}}},
			"usage":   map[string]int{"completion_tokens": 64},
		})
	}))
	t.Cleanup(backendServer.Close)
	registry, err := backend.New([]backend.Config{{
		ID: "gpu-0", URL: backendServer.URL, Models: []string{"demo-model"},
		Attributes: map[string]string{"gpu_type": "test-gpu", "engine": "vllm", "engine_version": "test-version"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	controller := router.NewController(router.ModeEnforced, nil)
	policy := &recordingPolicy{result: allowResult()}
	policy.result.EstimatedTokens = 1000
	server, err := gateway.New(gateway.Config{
		Backends: registry, Policy: policy, Router: controller, RequestTimeout: time.Second,
		MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20, TokenBudget: 2048,
	})
	if err != nil {
		t.Fatal(err)
	}
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	requestID := response.Header.Get("X-Request-ID")
	decision, ok := controller.Ledger().Get(requestID)
	if !ok || decision.Outcome == nil || decision.PredictionError == nil {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
	if !decision.Outcome.Success || decision.Outcome.PromptTokens != 1000 || decision.Outcome.OutputTokens != 64 {
		t.Fatalf("outcome=%+v", decision.Outcome)
	}
	if decision.Outcome.Model != "demo-model" || decision.Outcome.GPUType != "test-gpu" || decision.Outcome.BackendEngine != "vllm" || decision.Outcome.BackendVersion != "test-version" {
		t.Fatalf("outcome dimensions=%+v", decision.Outcome)
	}
	if decision.Outcome.ObservedCacheHitRatio == nil || *decision.Outcome.ObservedCacheHitRatio != .75 {
		t.Fatalf("outcome=%+v", decision.Outcome)
	}
	if decision.Outcome.TTFTMS != 0.05 || decision.Outcome.E2EMS < decision.Outcome.TTFTMS {
		t.Fatalf("outcome=%+v", decision.Outcome)
	}
}

func TestGatewayDoesNotTreatBufferedResponseLatencyAsTTFT(t *testing.T) {
	backendServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"choices":[]}`))
	}))
	t.Cleanup(backendServer.Close)
	registry, err := backend.New([]backend.Config{{ID: "gpu-0", URL: backendServer.URL, Models: []string{"demo-model"}}})
	if err != nil {
		t.Fatal(err)
	}
	controller := router.NewController(router.ModeEnforced, nil)
	server, err := gateway.New(gateway.Config{
		Backends: registry, Policy: &recordingPolicy{result: allowResult()}, Router: controller, RequestTimeout: time.Second,
		MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20, TokenBudget: 2048,
	})
	if err != nil {
		t.Fatal(err)
	}
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	decision, ok := controller.Ledger().Get(response.Header.Get("X-Request-ID"))
	if !ok || decision.Outcome == nil || decision.Outcome.TTFTMS != 0 || decision.PredictionError != nil {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
}

func TestGatewayRecordsFailedBackendOutcome(t *testing.T) {
	backendServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusServiceUnavailable)
	}))
	t.Cleanup(backendServer.Close)
	registry, err := backend.New([]backend.Config{{ID: "gpu-0", URL: backendServer.URL, Models: []string{"demo-model"}}})
	if err != nil {
		t.Fatal(err)
	}
	controller := router.NewController(router.ModeEnforced, nil)
	server, err := gateway.New(gateway.Config{
		Backends: registry, Policy: &recordingPolicy{result: allowResult()}, Router: controller, RequestTimeout: time.Second,
		MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20, TokenBudget: 2048,
	})
	if err != nil {
		t.Fatal(err)
	}
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	decision, ok := controller.Ledger().Get(response.Header.Get("X-Request-ID"))
	if !ok || decision.Outcome == nil || decision.Outcome.Success || decision.Outcome.StatusCode != http.StatusBadGateway || decision.Outcome.ActualBackend != "gpu-0" {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
}
