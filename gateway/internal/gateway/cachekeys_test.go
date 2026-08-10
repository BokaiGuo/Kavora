package gateway

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHTTPVLLMCacheKeyResolverReturnsAlignedBlockKeys(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/cache-keys" {
			http.NotFound(writer, request)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"cache_keys":["vllm:block:aa","vllm:block:bb"],"token_count":40,"full_blocks":2}`))
	}))
	t.Cleanup(server.Close)
	resolver, err := NewHTTPVLLMCacheKeyResolver(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}

	result, err := resolver.Resolve(context.Background(), []byte(`{"model":"m","messages":[{"role":"user","content":"hello"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if len(result.CacheKeys) != 2 || result.CacheKeys[1] != "vllm:block:bb" || result.TokenCount != 40 {
		t.Fatalf("result=%+v", result)
	}
}

func TestHTTPVLLMCacheKeyResolverRejectsInconsistentCounts(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"cache_keys":["vllm:block:aa"],"token_count":40,"full_blocks":2}`))
	}))
	t.Cleanup(server.Close)
	resolver, err := NewHTTPVLLMCacheKeyResolver(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}

	if _, err := resolver.Resolve(context.Background(), []byte(`{"model":"m"}`)); err == nil {
		t.Fatal("expected inconsistent resolver counts to be rejected")
	}
}
