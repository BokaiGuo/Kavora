package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
)

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
