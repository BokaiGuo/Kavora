package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
)

func TestConfigureOutcomeGroundingLoadsJournalAndPredictor(t *testing.T) {
	directory := t.TempDir()
	artifactPath := filepath.Join(directory, "predictor.json")
	artifact := `{"schema_version":"kavora-ttft-predictor/v1","predictor_version":"fitted-v1","model":"demo","gpu_type":"test-gpu","backend_engine":"vllm","backend_version":"test-version","coefficients":{"intercept_ms":10,"uncached_token_ms":0.1,"cached_token_ms":0.01,"queue_penalty_ms":4,"kv_pressure_penalty_ms":20,"slo_scale_ms":25},"validation":{"mae_ms":12,"p95_absolute_error_ms":30,"samples":100}}`
	if err := os.WriteFile(artifactPath, []byte(artifact), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("KAVORA_DECISION_JOURNAL_DIR", filepath.Join(directory, "journal"))
	t.Setenv("KAVORA_TTFT_PREDICTOR_PATH", artifactPath)
	controller := router.NewController(router.ModeEnforced, nil)
	if err := configureOutcomeGrounding(controller); err != nil {
		t.Fatal(err)
	}
	decision := controller.Plan(context.Background(), router.RoutingRequest{RequestID: "req", Model: "demo", PromptTokens: 100}, []router.BackendDescriptor{{ID: "gpu", Attributes: map[string]string{"gpu_type": "test-gpu", "engine": "vllm", "engine_version": "test-version"}}})
	if decision.PredictorVersion != "fitted-v1" {
		t.Fatalf("decision=%+v", decision)
	}
	controller.Ledger().Record(router.Decision{RequestID: "journal-test"})
	if err := controller.Ledger().Err(); err != nil {
		t.Fatal(err)
	}
	paths, err := filepath.Glob(filepath.Join(directory, "journal", "decisions-*.jsonl"))
	if err != nil || len(paths) != 1 {
		t.Fatalf("paths=%v err=%v", paths, err)
	}
}

func TestPollBackendStateOnceUpdatesMultipleBackends(t *testing.T) {
	serverFor := func(id string, queue float64) *httptest.Server {
		snapshot := backendstate.Snapshot{
			SchemaVersion:        backendstate.SchemaVersion,
			BackendID:            id,
			Backend:              "vllm",
			Model:                "demo",
			ObservedAtUnixMillis: time.Now().UnixMilli(),
			Signals: map[string]backendstate.Signal{
				"queue_depth": {Value: queue, HasValue: true, Quality: "fresh", Source: "test", ObservedAtUnixMillis: time.Now().UnixMilli()},
			},
		}
		return httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
			_ = json.NewEncoder(writer).Encode(snapshot)
		}))
	}
	first := serverFor("gpu-0", 4)
	defer first.Close()
	second := serverFor("gpu-1", 1)
	defer second.Close()

	controller := router.NewController(router.ModeLoadAware, nil)
	err := pollBackendStateOnce(context.Background(), http.DefaultClient, controller, []string{first.URL, second.URL})
	if err != nil {
		t.Fatal(err)
	}
	decision := controller.Decide("req", "tenant", "cache")
	if decision.Selected != "gpu-1" || decision.Fallback {
		t.Fatalf("decision=%+v", decision)
	}
}

func TestBackendStateURLsAcceptCommaAndWhitespace(t *testing.T) {
	got := backendStateURLs("http://a.test/backend-state, http://b.test/backend-state\nhttp://c.test/backend-state")
	if len(got) != 3 || got[1] != "http://b.test/backend-state" {
		t.Fatalf("URLs=%v", got)
	}
}
