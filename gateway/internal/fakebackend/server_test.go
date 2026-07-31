package fakebackend_test

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
)

func TestChatCompletionReturnsDeterministicResponse(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"hello", " world"}})
	server := httptest.NewServer(backend)
	t.Cleanup(server.Close)

	response := postChatRequest(t, server.URL, false, nil)
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
	}

	var payload struct {
		ID      string `json:"id"`
		Object  string `json:"object"`
		Created int64  `json:"created"`
		Model   string `json:"model"`
		Choices []struct {
			Message struct {
				Role    string `json:"role"`
				Content string `json:"content"`
			} `json:"message"`
			FinishReason string `json:"finish_reason"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if payload.ID != "chatcmpl-kavora-fake" || payload.Object != "chat.completion" || payload.Created != 0 {
		t.Fatalf("unexpected response identity: %+v", payload)
	}
	if payload.Model != "demo-model" {
		t.Fatalf("model = %q, want demo-model", payload.Model)
	}
	if len(payload.Choices) != 1 || payload.Choices[0].Message.Role != "assistant" {
		t.Fatalf("unexpected choices: %+v", payload.Choices)
	}
	if payload.Choices[0].Message.Content != "hello world" || payload.Choices[0].FinishReason != "stop" {
		t.Fatalf("unexpected completion: %+v", payload.Choices[0])
	}
}

func TestStreamEmitsOrderedSSEAndDone(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"hello", " world"}})
	server := httptest.NewServer(backend)
	t.Cleanup(server.Close)

	response := postChatRequest(t, server.URL, true, nil)
	defer response.Body.Close()

	events := readSSEData(t, response.Body)
	if len(events) != 3 {
		t.Fatalf("events = %q, want two chunks and DONE", events)
	}
	if events[2] != "[DONE]" {
		t.Fatalf("last event = %q, want [DONE]", events[2])
	}

	var first, second streamChunk
	if err := json.Unmarshal([]byte(events[0]), &first); err != nil {
		t.Fatalf("decode first chunk: %v", err)
	}
	if err := json.Unmarshal([]byte(events[1]), &second); err != nil {
		t.Fatalf("decode second chunk: %v", err)
	}
	if first.Choices[0].Delta.Role != "assistant" || first.Choices[0].Delta.Content != "hello" {
		t.Fatalf("unexpected first chunk: %+v", first)
	}
	if second.Choices[0].Delta.Content != " world" || second.Choices[0].FinishReason != "stop" {
		t.Fatalf("unexpected second chunk: %+v", second)
	}
}

func TestStreamCanFailAfterConfiguredChunk(t *testing.T) {
	t.Parallel()

	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks:  []string{"first", "second"},
		FailAfterChunks: 1,
	})
	server := httptest.NewServer(backend)
	t.Cleanup(server.Close)

	response := postChatRequest(t, server.URL, true, nil)
	defer response.Body.Close()

	events := readSSEData(t, response.Body)
	if len(events) != 1 {
		t.Fatalf("events = %q, want one chunk before truncation", events)
	}
	if events[0] == "[DONE]" {
		t.Fatal("truncated stream must not emit DONE")
	}
}

func TestCanceledRequestIsObservedDuringTTFT(t *testing.T) {
	backend := fakebackend.New(fakebackend.Config{
		ResponseChunks: []string{"never emitted"},
		TTFT:           time.Minute,
	})
	server := httptest.NewServer(backend)
	t.Cleanup(server.Close)

	ctx, cancel := context.WithCancel(context.Background())
	requestDone := make(chan error, 1)
	go func() {
		response, err := postChatRequestWithError(ctx, server.URL, true)
		if response != nil {
			response.Body.Close()
		}
		requestDone <- err
	}()

	deadline := time.Now().Add(time.Second)
	for backend.StartedRequests() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if backend.StartedRequests() != 1 {
		t.Fatal("fake backend did not observe the request")
	}

	cancel()
	select {
	case <-requestDone:
	case <-time.After(time.Second):
		t.Fatal("client request did not stop after cancellation")
	}

	deadline = time.Now().Add(time.Second)
	for backend.CanceledRequests() != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if backend.CanceledRequests() != 1 {
		t.Fatalf("canceled requests = %d, want 1", backend.CanceledRequests())
	}
}

type streamChunk struct {
	Choices []struct {
		Delta struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"delta"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
}

func postChatRequest(t *testing.T, baseURL string, stream bool, ctx context.Context) *http.Response {
	t.Helper()
	if ctx == nil {
		ctx = context.Background()
	}
	response, err := postChatRequestWithError(ctx, baseURL, stream)
	if err != nil {
		t.Fatalf("post chat request: %v", err)
	}
	return response
}

func postChatRequestWithError(ctx context.Context, baseURL string, stream bool) (*http.Response, error) {
	body, err := json.Marshal(map[string]any{
		"model":    "demo-model",
		"messages": []map[string]string{{"role": "user", "content": "hi"}},
		"stream":   stream,
	})
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	return http.DefaultClient.Do(request)
}

func readSSEData(t *testing.T, reader io.Reader) []string {
	t.Helper()

	var events []string
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		line := scanner.Text()
		if data, ok := strings.CutPrefix(line, "data: "); ok {
			events = append(events, data)
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("read SSE stream: %v", err)
	}
	return events
}

func TestFakeBackendHealthEndpoint(t *testing.T) {
	server := httptest.NewServer(fakebackend.New(fakebackend.Config{}))
	defer server.Close()
	response, err := http.Get(server.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", response.StatusCode)
	}
}
