package client

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

func TestChatAddsAuthenticationAndParsesResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer secret" {
			t.Fatalf("authorization = %q", request.Header.Get("Authorization"))
		}
		writer.Header().Set("X-Request-ID", "req_test")
		writer.Header().Set("Content-Type", "application/json")
		fmt.Fprint(writer, `{"id":"chat_1","choices":[{"message":{"role":"assistant","content":"hello"}}]}`)
	}))
	defer server.Close()

	response, err := New(server.URL, "secret").Chat(context.Background(), ChatRequest{Model: "demo", Message: "hi"})
	if err != nil {
		t.Fatalf("chat: %v", err)
	}
	if response.Content != "hello" || response.RequestID != "req_test" {
		t.Fatalf("unexpected response: %+v", response)
	}
}

func TestStreamChatParsesSSEContent(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(writer, "data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n")
		fmt.Fprint(writer, "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n")
		fmt.Fprint(writer, "data: [DONE]\n\n")
	}))
	defer server.Close()

	var chunks []string
	response, err := New(server.URL, "secret").StreamChat(context.Background(), ChatRequest{Model: "demo", Message: "hi"}, func(chunk string) error {
		chunks = append(chunks, chunk)
		return nil
	})
	if err != nil {
		t.Fatalf("stream chat: %v", err)
	}
	if !reflect.DeepEqual(chunks, []string{"hello", " world"}) || response.Content != "hello world" {
		t.Fatalf("chunks = %q, response = %+v", chunks, response)
	}
}

func TestChatReturnsGatewayError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusForbidden)
		fmt.Fprint(writer, `{"error":{"code":"PII_DETECTED","message":"blocked","request_id":"req_blocked"}}`)
	}))
	defer server.Close()

	_, err := New(server.URL, "secret").Chat(context.Background(), ChatRequest{Model: "demo", Message: "hi"})
	apiError, ok := err.(*APIError)
	if !ok || apiError.Code != "PII_DETECTED" || apiError.RequestID != "req_blocked" {
		t.Fatalf("error = %#v", err)
	}
}

func TestBackendsParsesStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/backends" {
			t.Fatalf("path = %s", request.URL.Path)
		}
		fmt.Fprint(writer, `{"backends":[{"id":"vllm","healthy":true,"weight":2}]}`)
	}))
	defer server.Close()
	backends, err := New(server.URL, "").Backends(context.Background())
	if err != nil || len(backends) != 1 || backends[0].ID != "vllm" || !backends[0].Healthy {
		t.Fatalf("backends = %+v, err = %v", backends, err)
	}
}
