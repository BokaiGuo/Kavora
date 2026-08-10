package gateway_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
)

type fixedCacheKeyResolver struct{}

func (fixedCacheKeyResolver) Resolve(context.Context, []byte) (gateway.ResolvedCacheKeys, error) {
	return gateway.ResolvedCacheKeys{CacheKeys: []string{"vllm:block:a", "vllm:block:b"}, TokenCount: 32, FullBlocks: 2}, nil
}

func TestGatewayUsesAlignedVLLMBlockKeysForExactPlacement(t *testing.T) {
	backendFor := func(id string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
			_ = json.NewEncoder(writer).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]string{"content": id}}}})
		}))
	}
	first := backendFor("gpu-0")
	defer first.Close()
	second := backendFor("gpu-1")
	defer second.Close()
	registry, err := backend.New([]backend.Config{
		{ID: "gpu-0", URL: first.URL, Models: []string{"demo-model"}},
		{ID: "gpu-1", URL: second.URL, Models: []string{"demo-model"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	provider := router.NewKVEventProvider(16, time.Minute, .1, time.Now)
	for index, key := range []string{"vllm:block:a", "vllm:block:b"} {
		provider.Observe(router.KVEvent{Operation: "store", BackendID: "gpu-1", CacheKey: key, MatchedTokens: 16, TotalTokens: 16, Sequence: uint64(index), HasSequence: true, Generation: "gen", EngineEventID: key, ObservedAt: time.Now()})
	}
	controller := router.NewController(router.ModeEnforced, nil)
	controller.SetCacheProvider(provider)
	server, err := gateway.New(gateway.Config{
		Backends: registry, Policy: &recordingPolicy{result: allowResult()}, Router: controller, CacheKeyResolver: fixedCacheKeyResolver{}, RequestTimeout: time.Second,
		MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20, TokenBudget: 2048,
	})
	if err != nil {
		t.Fatal(err)
	}
	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	if response.Header.Get("X-Kavora-Backend") != "gpu-1" || response.Header.Get("X-Kavora-Hash-Alignment") != "vllm-exact" {
		t.Fatalf("headers=%v", response.Header)
	}
	decision, ok := controller.Ledger().Get(response.Header.Get("X-Request-ID"))
	if !ok || decision.HashAlignment != "vllm_external_block_hash" || decision.ExternalCacheKeyCount != 2 || decision.Candidates[0].MatchedTokens != 32 {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
}
