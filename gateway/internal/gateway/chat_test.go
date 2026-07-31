package gateway_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policycontract"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

func TestAllowedRequestIsForwardedToBackend(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"allowed"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &recordingPolicy{result: allowResult()}
	server := newGateway(t, backendServer.URL, policy)

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	if response.Header.Get("X-Request-ID") == "" {
		t.Fatal("response must include a generated request ID")
	}
	var payload struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if payload.Choices[0].Message.Content != "allowed" {
		t.Fatalf("content = %q, want allowed", payload.Choices[0].Message.Content)
	}
	if policy.request == nil || policy.request.Context.RequestId == "" {
		t.Fatal("policy request must contain the gateway request ID")
	}
	if policy.request.Context.RequestId != response.Header.Get("X-Request-ID") {
		t.Fatal("policy and HTTP response request IDs must match")
	}
	if backend.StartedRequests() != 1 {
		t.Fatalf("backend requests = %d, want 1", backend.StartedRequests())
	}
}

func TestBlockedRequestNeverReachesBackend(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &recordingPolicy{result: &policyv1.PolicyResult{
		Decision:       policyv1.Decision_DECISION_BLOCK,
		ErrorCode:      policyv1.PolicyErrorCode_POLICY_ERROR_CODE_PII_DETECTED,
		Reason:         "request contains an email address",
		MatchedRuleIds: []string{"pii.email"},
		PolicyVersion:  "policy-v1",
	}}
	server := newGateway(t, backendServer.URL, policy)

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"alice@example.com"}]}`)
	defer response.Body.Close()

	if response.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusForbidden)
	}
	var payload struct {
		Error struct {
			Code      string `json:"code"`
			RequestID string `json:"request_id"`
		} `json:"error"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode error response: %v", err)
	}
	if payload.Error.Code != "POLICY_ERROR_CODE_PII_DETECTED" {
		t.Fatalf("error code = %q", payload.Error.Code)
	}
	if payload.Error.RequestID == "" || payload.Error.RequestID != response.Header.Get("X-Request-ID") {
		t.Fatal("error body and header must contain the same request ID")
	}
	if backend.StartedRequests() != 0 {
		t.Fatalf("backend requests = %d, want 0", backend.StartedRequests())
	}
}

func TestClientCancellationReachesBackend(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks: []string{"never returned"},
		TTFT:           time.Minute,
	})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newGateway(t, backendServer.URL, &recordingPolicy{result: allowResult()})
	gatewayServer := httptest.NewServer(server)
	t.Cleanup(gatewayServer.Close)

	ctx, cancel := context.WithCancel(context.Background())
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		gatewayServer.URL+"/v1/chat/completions",
		bytes.NewBufferString(`{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`),
	)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	done := make(chan error, 1)
	go func() {
		response, requestErr := http.DefaultClient.Do(request)
		if response != nil {
			response.Body.Close()
		}
		done <- requestErr
	}()

	deadline := time.Now().Add(time.Second)
	for backend.StartedRequests() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if backend.StartedRequests() != 1 {
		t.Fatal("backend did not receive request")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("client request did not stop after cancellation")
	}
	deadline = time.Now().Add(time.Second)
	for backend.CanceledRequests() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if backend.CanceledRequests() != 1 {
		t.Fatalf("backend cancellations = %d, want 1", backend.CanceledRequests())
	}
}

func TestGatewayDeadlineCancelsPolicyEvaluation(t *testing.T) {
	policy := &blockingPolicy{canceled: make(chan struct{})}
	backend := fakebackend.New(fakebackend.Config{})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server, err := gateway.New(gateway.Config{
		BackendURL:       backendServer.URL,
		Policy:           policy,
		RequestTimeout:   20 * time.Millisecond,
		MaxRequestBytes:  1 << 20,
		MaxResponseBytes: 1 << 20,
		TokenBudget:      1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusServiceUnavailable)
	}
	select {
	case <-policy.canceled:
	default:
		t.Fatal("policy evaluator did not observe gateway deadline")
	}
	if backend.StartedRequests() != 0 {
		t.Fatalf("backend requests = %d, want 0", backend.StartedRequests())
	}
}

func TestOversizedBackendResponseIsRejectedBeforeForwarding(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"response larger than limit"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server, err := gateway.New(gateway.Config{
		BackendURL:       backendServer.URL,
		Policy:           &recordingPolicy{result: allowResult()},
		RequestTimeout:   time.Second,
		MaxRequestBytes:  1 << 20,
		MaxResponseBytes: 16,
		TokenBudget:      1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusBadGateway)
	}
}

type recordingPolicy struct {
	request *policyv1.EvaluateRequestRequest
	result  *policyv1.PolicyResult
	err     error
}

type blockingPolicy struct {
	canceled chan struct{}
}

func (policy *blockingPolicy) Evaluate(ctx context.Context, _ *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	<-ctx.Done()
	close(policy.canceled)
	return nil, ctx.Err()
}

func (policy *recordingPolicy) Evaluate(_ context.Context, request *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	policy.request = request
	return policy.result, policy.err
}

func (policy *recordingPolicy) OpenStream(
	_ context.Context,
	_ *policyv1.EvaluateRequestRequest,
) (policycontract.StreamSession, *policyv1.PolicyResult, error) {
	return &allowingStreamSession{}, policy.result, policy.err
}

type allowingStreamSession struct {
	sequence uint64
}

func (session *allowingStreamSession) Check(_ context.Context, chunk []byte) (*policyv1.StreamCheckResponse, error) {
	session.sequence++
	return &policyv1.StreamCheckResponse{
		Sequence:     session.sequence,
		Result:       allowResult(),
		ReleaseBytes: uint64(len(chunk)),
	}, nil
}

func (session *allowingStreamSession) Close(context.Context) (*policyv1.StreamCheckResponse, error) {
	session.sequence++
	return &policyv1.StreamCheckResponse{Sequence: session.sequence, Result: allowResult()}, nil
}

func allowResult() *policyv1.PolicyResult {
	return &policyv1.PolicyResult{
		Decision:      policyv1.Decision_DECISION_ALLOW,
		PolicyVersion: "policy-v1",
	}
}

func newGateway(t *testing.T, backendURL string, policy gateway.PolicyEvaluator) http.Handler {
	t.Helper()
	streamPolicy, _ := policy.(policycontract.StreamEvaluator)
	server, err := gateway.New(gateway.Config{
		BackendURL:        backendURL,
		Policy:            policy,
		RequestTimeout:    time.Second,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  1 << 20,
		StreamChunkBytes:  16 << 10,
		StreamBufferBytes: 64 << 10,
		StreamPolicy:      streamPolicy,
		TokenBudget:       1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}
	return server
}

func postChat(t *testing.T, handler http.Handler, body string) *http.Response {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/chat/completions", bytes.NewBufferString(body))
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("send request: %v", err)
	}
	return response
}
