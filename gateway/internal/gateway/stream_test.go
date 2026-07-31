package gateway_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policycontract"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

func TestStreamPreservesOrderedSSEAndDone(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"hello", " world"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newGateway(t, backendServer.URL, &recordingPolicy{result: allowResult()})

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}
	if contentType := response.Header.Get("Content-Type"); !strings.HasPrefix(contentType, "text/event-stream") {
		t.Fatalf("content type = %q, want text/event-stream", contentType)
	}
	events := readGatewaySSE(t, response.Body)
	if len(events) != 3 || events[2] != "[DONE]" {
		t.Fatalf("events = %q, want two chunks and DONE", events)
	}
	var first, second struct {
		Choices []struct {
			Delta struct {
				Content string `json:"content"`
			} `json:"delta"`
		} `json:"choices"`
	}
	if err := json.Unmarshal([]byte(events[0]), &first); err != nil {
		t.Fatalf("decode first event: %v", err)
	}
	if err := json.Unmarshal([]byte(events[1]), &second); err != nil {
		t.Fatalf("decode second event: %v", err)
	}
	if first.Choices[0].Delta.Content != "hello" || second.Choices[0].Delta.Content != " world" {
		t.Fatalf("unexpected chunk order: %q then %q", first.Choices[0].Delta.Content, second.Choices[0].Delta.Content)
	}
}

func TestTruncatedBackendStreamDoesNotInventDoneOrRetry(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks:  []string{"first", "must not be sent"},
		FailAfterChunks: 1,
	})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newGateway(t, backendServer.URL, &recordingPolicy{result: allowResult()})

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want established stream status %d", response.StatusCode, http.StatusOK)
	}
	events := readGatewaySSE(t, response.Body)
	if len(events) != 1 || events[0] == "[DONE]" {
		t.Fatalf("events = %q, want one truncated data event", events)
	}
	if backend.StartedRequests() != 1 {
		t.Fatalf("backend requests = %d, want no transparent retry", backend.StartedRequests())
	}
}

func TestStreamFlushesEachBackendWrite(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks: []string{"first", "second"},
		ChunkInterval:  5 * time.Millisecond,
	})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newGateway(t, backendServer.URL, &recordingPolicy{result: allowResult()})

	request := httptest.NewRequest(
		http.MethodPost,
		"/v1/chat/completions",
		strings.NewReader(`{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`),
	)
	request.Header.Set("Content-Type", "application/json")
	writer := newFlushRecorder()

	server.ServeHTTP(writer, request)

	if writer.flushes < 2 {
		t.Fatalf("flushes = %d, want at least one per backend chunk", writer.flushes)
	}
}

func TestStreamCancellationStopsBackendBetweenChunks(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks: []string{"first", "never emitted"},
		ChunkInterval:  time.Minute,
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
		bytes.NewBufferString(`{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`),
	)
	if err != nil {
		t.Fatalf("create stream request: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("start stream request: %v", err)
	}

	reader := bufio.NewReader(response.Body)
	if _, err := reader.ReadString('\n'); err != nil {
		response.Body.Close()
		t.Fatalf("read first stream line: %v", err)
	}
	cancel()
	response.Body.Close()

	deadline := time.Now().Add(time.Second)
	for backend.CanceledRequests() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if backend.CanceledRequests() != 1 {
		t.Fatalf("backend cancellations = %d, want 1", backend.CanceledRequests())
	}
}

func TestStreamPolicyBufferIsBoundedWhenNothingCanBeReleased(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{strings.Repeat("x", 256)}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &holdingPolicy{}
	server, err := gateway.New(gateway.Config{
		BackendURL:        backendServer.URL,
		Policy:            policy,
		RequestTimeout:    time.Second,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  1 << 20,
		StreamChunkBytes:  16,
		StreamBufferBytes: 32,
		StreamPolicy:      policy,
		TokenBudget:       1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusBadGateway)
	}
	var payload struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode buffer error: %v", err)
	}
	if payload.Error.Code != "STREAM_POLICY_BUFFER_FULL" {
		t.Fatalf("error code = %q", payload.Error.Code)
	}
}

func TestStreamPolicyCheckHonorsGatewayDeadline(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"event"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	policy := &timeoutStreamPolicy{}
	server, err := gateway.New(gateway.Config{
		BackendURL:        backendServer.URL,
		Policy:            policy,
		RequestTimeout:    20 * time.Millisecond,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  1 << 20,
		StreamChunkBytes:  16 << 10,
		StreamBufferBytes: 64 << 10,
		StreamPolicy:      policy,
		TokenBudget:       1024,
	})
	if err != nil {
		t.Fatalf("create gateway: %v", err)
	}

	response := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	defer response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusServiceUnavailable)
	}
}

type holdingPolicy struct{}

func (*holdingPolicy) Evaluate(context.Context, *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	return allowResult(), nil
}

func (*holdingPolicy) OpenStream(
	context.Context,
	*policyv1.EvaluateRequestRequest,
) (policycontract.StreamSession, *policyv1.PolicyResult, error) {
	return &holdingSession{}, allowResult(), nil
}

type holdingSession struct{}

func (*holdingSession) Check(context.Context, []byte) (*policyv1.StreamCheckResponse, error) {
	return &policyv1.StreamCheckResponse{Result: allowResult()}, nil
}

func (*holdingSession) Close(context.Context) (*policyv1.StreamCheckResponse, error) {
	return &policyv1.StreamCheckResponse{Result: allowResult()}, nil
}

type timeoutStreamPolicy struct{}

func (*timeoutStreamPolicy) Evaluate(context.Context, *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	return allowResult(), nil
}

func (*timeoutStreamPolicy) OpenStream(
	context.Context,
	*policyv1.EvaluateRequestRequest,
) (policycontract.StreamSession, *policyv1.PolicyResult, error) {
	return &timeoutStreamSession{}, allowResult(), nil
}

type timeoutStreamSession struct{}

func (*timeoutStreamSession) Check(ctx context.Context, _ []byte) (*policyv1.StreamCheckResponse, error) {
	<-ctx.Done()
	return nil, ctx.Err()
}

func (*timeoutStreamSession) Close(ctx context.Context) (*policyv1.StreamCheckResponse, error) {
	return nil, ctx.Err()
}

type flushRecorder struct {
	*httptest.ResponseRecorder
	flushes int
}

func newFlushRecorder() *flushRecorder {
	return &flushRecorder{ResponseRecorder: httptest.NewRecorder()}
}

func (writer *flushRecorder) Flush() {
	writer.flushes++
	writer.ResponseRecorder.Flush()
}

func readGatewaySSE(t *testing.T, reader io.Reader) []string {
	t.Helper()

	var events []string
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		if data, ok := strings.CutPrefix(scanner.Text(), "data: "); ok {
			events = append(events, data)
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("read SSE: %v", err)
	}
	return events
}
