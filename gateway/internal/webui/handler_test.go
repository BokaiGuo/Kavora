package webui

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/telemetry"
)

func TestHandlerServesUIAndHealth(t *testing.T) {
	handler := New(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	}))

	health := httptest.NewRecorder()
	handler.ServeHTTP(health, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if health.Code != http.StatusOK || !strings.Contains(health.Body.String(), `"status":"ok"`) {
		t.Fatalf("health response = %d %q", health.Code, health.Body.String())
	}

	page := httptest.NewRecorder()
	handler.ServeHTTP(page, httptest.NewRequest(http.MethodGet, "/ui/", nil))
	if page.Code != http.StatusOK || !strings.Contains(page.Body.String(), "Kavora") {
		t.Fatalf("UI response = %d %q", page.Code, page.Body.String())
	}
}

func TestHandlerRequiresExplicitLifecycleApproval(t *testing.T) {
	lifecycle, err := router.NewLifecycle(router.LifecycleConfig{Enabled: true, RequireHumanApproval: true})
	if err != nil {
		t.Fatal(err)
	}
	controller := router.NewController(router.ModeShadow, nil)
	controller.SetLifecycle(lifecycle)
	handler := NewWithControlPlane(http.NotFoundHandler(), nil, nil, nil, controller, "secret")
	request := httptest.NewRequest(http.MethodPost, "/v1/admin/lifecycle/approve", bytes.NewBufferString(`{"approved_by":"operator"}`))
	request.Header.Set("Authorization", "Bearer secret")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"approved":true`) {
		t.Fatalf("response=%d %q", response.Code, response.Body.String())
	}
}

func TestHandlerServesDecisionLedger(t *testing.T) {
	controller := router.NewController(router.ModeStatic, nil)
	controller.Ledger().Record(router.Decision{RequestID: "req-1", Reason: "test", OccurredAt: time.Now()})
	handler := NewWithControlPlane(http.NotFoundHandler(), nil, nil, nil, controller, "secret")
	request := httptest.NewRequest(http.MethodGet, "/v1/admin/decisions/req-1", nil)
	request.Header.Set("Authorization", "Bearer secret")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"request_id":"req-1"`) {
		t.Fatalf("response=%d %q", response.Code, response.Body.String())
	}
}

func TestHandlerDelegatesGatewayAPI(t *testing.T) {
	called := false
	handler := New(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		called = true
		writer.WriteHeader(http.StatusAccepted)
	}))
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil))
	if !called || response.Code != http.StatusAccepted {
		t.Fatalf("delegated = %v, status = %d", called, response.Code)
	}
}

func TestHandlerServesMetricsAndReadiness(t *testing.T) {
	metrics := telemetry.NewMetrics()
	metrics.IncRequest("/v1/chat/completions", "success")
	handler := NewWithObservability(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {}), metrics, func() bool { return false }, func() any { return []string{"backend"} })

	metricsResponse := httptest.NewRecorder()
	handler.ServeHTTP(metricsResponse, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if metricsResponse.Code != http.StatusOK || !strings.Contains(metricsResponse.Body.String(), "kavora_requests_total") {
		t.Fatalf("metrics response = %d %q", metricsResponse.Code, metricsResponse.Body.String())
	}
	readyResponse := httptest.NewRecorder()
	handler.ServeHTTP(readyResponse, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if readyResponse.Code != http.StatusServiceUnavailable || !strings.Contains(readyResponse.Body.String(), `"ready":false`) {
		t.Fatalf("readiness response = %d %q", readyResponse.Code, readyResponse.Body.String())
	}
}

func TestHandlerServesBackendStatus(t *testing.T) {
	handler := NewWithObservability(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {}), nil, nil, func() any {
		return []map[string]any{{"id": "vllm", "healthy": true}}
	})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/backends", nil))
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"id":"vllm"`) {
		t.Fatalf("backend response = %d %q", response.Code, response.Body.String())
	}
}
