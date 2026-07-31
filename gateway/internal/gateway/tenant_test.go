package gateway_test

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policycontract"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/tenant"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

func TestAuthenticatedTenantControlsPolicyContext(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"allowed"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &recordingPolicy{result: allowResult()}
	server := newTenantGateway(t, backendServer.URL, policy, `
tenants:
  - id: team-alpha
    api_keys: [alpha-key]
    max_concurrent: 2
    token_budget: 321
    policy_fail_mode: open
`)

	response := postAuthenticatedChat(t, server, "alpha-key")
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	context := policy.request.GetContext()
	if context.GetTenantId() != "team-alpha" || context.GetTokenBudget() != 321 {
		t.Fatalf("unexpected policy context: %+v", context)
	}
	if context.GetFailMode() != policyv1.FailMode_FAIL_MODE_OPEN {
		t.Fatalf("fail mode = %s", context.GetFailMode())
	}
}

func TestMissingOrInvalidAPIKeyIsUnauthorized(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"not reached"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newTenantGateway(t, backendServer.URL, &recordingPolicy{result: allowResult()}, singleTenantConfig("closed"))

	for _, authorization := range []string{"", "Bearer wrong-key"} {
		request := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(chatBody))
		request.Header.Set("Authorization", authorization)
		writer := httptest.NewRecorder()
		server.ServeHTTP(writer, request)
		if writer.Code != http.StatusUnauthorized {
			t.Fatalf("authorization %q status = %d", authorization, writer.Code)
		}
	}
	if backend.StartedRequests() != 0 {
		t.Fatalf("backend requests = %d, want 0", backend.StartedRequests())
	}
}

func TestPolicyFailureUsesTenantFailMode(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"allowed"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)

	for _, test := range []struct {
		mode       string
		wantStatus int
	}{
		{mode: "open", wantStatus: http.StatusOK},
		{mode: "closed", wantStatus: http.StatusServiceUnavailable},
	} {
		t.Run(test.mode, func(t *testing.T) {
			policy := &recordingPolicy{err: errors.New("policy offline")}
			server := newTenantGateway(t, backendServer.URL, policy, singleTenantConfig(test.mode))
			response := postAuthenticatedChat(t, server, "tenant-key")
			defer response.Body.Close()
			if response.StatusCode != test.wantStatus {
				t.Fatalf("status = %d, want %d", response.StatusCode, test.wantStatus)
			}
		})
	}
}

func TestStreamPolicyOpenFailureUsesTenantFailMode(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"allowed"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	for _, test := range []struct {
		mode       string
		wantStatus int
	}{
		{mode: "open", wantStatus: http.StatusOK},
		{mode: "closed", wantStatus: http.StatusServiceUnavailable},
	} {
		t.Run(test.mode, func(t *testing.T) {
			policy := &streamOpenFailurePolicy{}
			registry, err := tenant.Load(strings.NewReader(singleTenantConfig(test.mode)))
			if err != nil {
				t.Fatal(err)
			}
			server, err := gateway.New(gateway.Config{
				BackendURL: backendServer.URL, Policy: policy, StreamPolicy: policy, Tenants: registry,
				RequestTimeout: time.Second, MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20,
				StreamChunkBytes: 16 << 10, StreamBufferBytes: 64 << 10,
			})
			if err != nil {
				t.Fatal(err)
			}
			response := postAuthenticatedBody(t, server, "tenant-key", strings.TrimSuffix(chatBody, "}")+`,"stream":true}`)
			defer response.Body.Close()
			if response.StatusCode != test.wantStatus {
				t.Fatalf("status = %d, want %d", response.StatusCode, test.wantStatus)
			}
		})
	}
}

func TestTenantConcurrencyLimitRejectsOnlySaturatedTenant(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"allowed"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &gatedPolicy{entered: make(chan struct{}), release: make(chan struct{})}
	registry, err := tenant.Load(strings.NewReader(`
tenants:
  - id: alpha
    api_keys: [alpha-key]
    max_concurrent: 1
    token_budget: 100
    policy_fail_mode: closed
  - id: beta
    api_keys: [beta-key]
    max_concurrent: 1
    token_budget: 100
    policy_fail_mode: closed
`))
	if err != nil {
		t.Fatal(err)
	}
	server, err := gateway.New(gateway.Config{BackendURL: backendServer.URL, Policy: policy, Tenants: registry, RequestTimeout: time.Second, MaxRequestBytes: 1 << 20, MaxResponseBytes: 1 << 20})
	if err != nil {
		t.Fatal(err)
	}
	firstDone := make(chan *http.Response)
	go func() { firstDone <- postAuthenticatedChat(t, server, "alpha-key") }()
	<-policy.entered

	alpha := postAuthenticatedChat(t, server, "alpha-key")
	alpha.Body.Close()
	if alpha.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("second alpha status = %d", alpha.StatusCode)
	}
	close(policy.release)
	first := <-firstDone
	first.Body.Close()
}

const chatBody = `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`

func singleTenantConfig(failMode string) string {
	return "tenants:\n  - id: tenant\n    api_keys: [tenant-key]\n    max_concurrent: 1\n    token_budget: 100\n    policy_fail_mode: " + failMode + "\n"
}

func newTenantGateway(t *testing.T, backendURL string, policy *recordingPolicy, config string) http.Handler {
	t.Helper()
	registry, err := tenant.Load(strings.NewReader(config))
	if err != nil {
		t.Fatalf("load tenant config: %v", err)
	}
	server, err := gateway.New(gateway.Config{
		BackendURL:        backendURL,
		Policy:            policy,
		RequestTimeout:    time.Second,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  1 << 20,
		StreamChunkBytes:  16 << 10,
		StreamBufferBytes: 64 << 10,
		StreamPolicy:      policy,
		Tenants:           registry,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}
	return server
}

func postAuthenticatedChat(t *testing.T, handler http.Handler, apiKey string) *http.Response {
	return postAuthenticatedBody(t, handler, apiKey, chatBody)
}

func postAuthenticatedBody(t *testing.T, handler http.Handler, apiKey string, body string) *http.Response {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/chat/completions", bytes.NewBufferString(body))
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	request.Header.Set("Authorization", "Bearer "+apiKey)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("send request: %v", err)
	}
	return response
}

type streamOpenFailurePolicy struct{}

func (*streamOpenFailurePolicy) Evaluate(context.Context, *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	return allowResult(), nil
}

func (*streamOpenFailurePolicy) OpenStream(context.Context, *policyv1.EvaluateRequestRequest) (policycontract.StreamSession, *policyv1.PolicyResult, error) {
	return nil, nil, errors.New("stream policy offline")
}

type gatedPolicy struct {
	entered chan struct{}
	release chan struct{}
}

func (policy *gatedPolicy) Evaluate(ctx context.Context, _ *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	select {
	case policy.entered <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	select {
	case <-policy.release:
		return allowResult(), nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}
