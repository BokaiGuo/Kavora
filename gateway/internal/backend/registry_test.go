package backend

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRegistryFiltersDisabledUnhealthyAndModelMismatch(t *testing.T) {
	registry, err := New([]Config{
		{ID: "vllm", URL: "http://vllm", Models: []string{"qwen"}, Weight: 2},
		{ID: "sglang", URL: "http://sglang", Models: []string{"qwen"}, Weight: 1},
		{ID: "disabled", URL: "http://disabled", Enabled: boolPointer(false)},
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := registry.Candidates("other-model"); len(got) != 0 {
		t.Fatalf("model mismatch candidates = %+v", got)
	}
	got := registry.Candidates("qwen")
	if len(got) != 2 || !containsBackend(got, "vllm") || !containsBackend(got, "sglang") {
		t.Fatalf("candidates = %+v", got)
	}
	registry.MarkFailure("vllm")
	got = registry.Candidates("qwen")
	if len(got) != 1 || got[0].ID != "sglang" {
		t.Fatalf("after failure candidates = %+v", got)
	}
	registry.MarkSuccess("vllm")
	if len(registry.Candidates("qwen")) != 2 {
		t.Fatal("successful backend was not restored")
	}
}

func containsBackend(backends []Backend, id string) bool {
	for _, current := range backends {
		if current.ID == id {
			return true
		}
	}
	return false
}

func boolPointer(value bool) *bool { return &value }

func okHealth() http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusNoContent)
	})
}

func TestRegistryValidatesBackendConfig(t *testing.T) {
	for _, config := range [][]Config{
		{{ID: "", URL: "http://backend"}},
		{{ID: "backend", URL: "://bad"}},
		{{ID: "backend", URL: "http://backend", Weight: -1}},
		{{ID: "backend", URL: "http://backend", Models: []string{""}}},
	} {
		if _, err := New(config); err == nil {
			t.Fatalf("config %+v unexpectedly succeeded", config)
		}
	}
}

func TestHealthCheckerRestoresBackend(t *testing.T) {
	server := httptest.NewServer(okHealth())
	defer server.Close()
	registry, err := New([]Config{{ID: "backend", URL: server.URL, HealthPath: "/healthz"}})
	if err != nil {
		t.Fatal(err)
	}
	registry.MarkFailure("backend")
	if err := registry.CheckHealth(t.Context(), server.Client()); err != nil {
		t.Fatal(err)
	}
	if len(registry.Candidates("any-model")) != 1 {
		t.Fatal("health check did not restore backend")
	}
}
