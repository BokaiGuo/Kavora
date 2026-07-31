package gateway_test

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
)

func TestNonStreamFailoverBeforeResponse(t *testing.T) {
	failing := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer failing.Close()
	working := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"backup"}})
	workingServer := httptest.NewServer(working)
	defer workingServer.Close()
	registry, err := backend.New([]backend.Config{
		{ID: "a-failing", URL: failing.URL},
		{ID: "b-working", URL: workingServer.URL},
	})
	if err != nil {
		t.Fatal(err)
	}
	server := newFailoverGateway(t, registry)
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	if working.StartedRequests() != 1 {
		t.Fatalf("working backend requests = %d", working.StartedRequests())
	}
}

func TestStreamFailoverBeforeFirstResponse(t *testing.T) {
	failing := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusBadGateway)
	}))
	defer failing.Close()
	working := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"stream backup"}})
	workingServer := httptest.NewServer(working)
	defer workingServer.Close()
	registry, err := backend.New([]backend.Config{
		{ID: "a-failing", URL: failing.URL},
		{ID: "b-working", URL: workingServer.URL},
	})
	if err != nil {
		t.Fatal(err)
	}
	server := newFailoverGateway(t, registry)
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	if working.StartedRequests() != 1 {
		t.Fatalf("working backend requests = %d", working.StartedRequests())
	}
}

func newFailoverGateway(t *testing.T, registry *backend.Registry) http.Handler {
	t.Helper()
	server, err := gateway.New(gateway.Config{
		Backends:          registry,
		Policy:            &recordingPolicy{result: allowResult()},
		RequestTimeout:    time.Second,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  1 << 20,
		StreamChunkBytes:  16 << 10,
		StreamBufferBytes: 64 << 10,
		StreamPolicy:      &recordingPolicy{result: allowResult()},
		TokenBudget:       1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}
	return server
}
