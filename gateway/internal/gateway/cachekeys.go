package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
)

type ResolvedCacheKeys struct {
	CacheKeys  []string `json:"cache_keys"`
	TokenCount int      `json:"token_count"`
	FullBlocks int      `json:"full_blocks"`
}

type CacheKeyResolver interface {
	Resolve(context.Context, []byte) (ResolvedCacheKeys, error)
}

type HTTPVLLMCacheKeyResolver struct {
	endpoint *url.URL
	client   *http.Client
}

func NewHTTPVLLMCacheKeyResolver(endpoint string, client *http.Client) (*HTTPVLLMCacheKeyResolver, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, errors.New("vLLM cache-key resolver URL must be absolute")
	}
	if client == nil {
		client = http.DefaultClient
	}
	return &HTTPVLLMCacheKeyResolver{endpoint: parsed.ResolveReference(&url.URL{Path: "/v1/cache-keys"}), client: client}, nil
}

func (resolver *HTTPVLLMCacheKeyResolver) Resolve(ctx context.Context, body []byte) (ResolvedCacheKeys, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, resolver.endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return ResolvedCacheKeys{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := resolver.client.Do(request)
	if err != nil {
		return ResolvedCacheKeys{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1<<20))
		return ResolvedCacheKeys{}, errors.New("vLLM cache-key resolver rejected request")
	}
	var result ResolvedCacheKeys
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return ResolvedCacheKeys{}, err
	}
	if result.TokenCount < 0 || result.FullBlocks < 0 || result.FullBlocks != len(result.CacheKeys) {
		return ResolvedCacheKeys{}, errors.New("vLLM cache-key resolver returned inconsistent counts")
	}
	for _, key := range result.CacheKeys {
		if len(key) <= len("vllm:block:") || key[:len("vllm:block:")] != "vllm:block:" {
			return ResolvedCacheKeys{}, errors.New("vLLM cache-key resolver returned an invalid key")
		}
	}
	return result, nil
}
