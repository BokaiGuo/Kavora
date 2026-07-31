package fakebackend

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"time"
)

const (
	completionID = "chatcmpl-kavora-fake"
	maxBodyBytes = 1 << 20
)

type Config struct {
	ResponseChunks  []string
	TTFT            time.Duration
	ChunkInterval   time.Duration
	FailAfterChunks int
}

type Server struct {
	config           Config
	startedRequests  atomic.Uint64
	canceledRequests atomic.Uint64
}

func New(config Config) *Server {
	if len(config.ResponseChunks) == 0 {
		config.ResponseChunks = []string{"ok"}
	}
	return &Server{config: config}
}

func (server *Server) StartedRequests() uint64 {
	return server.startedRequests.Load()
}

func (server *Server) CanceledRequests() uint64 {
	return server.canceledRequests.Load()
}

func (server *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	if request.Method == http.MethodGet && (request.URL.Path == "/healthz" || request.URL.Path == "/health") {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"status":"ok","service":"kavora-fake-backend"}`))
		return
	}
	if request.Method != http.MethodPost || request.URL.Path != "/v1/chat/completions" {
		http.NotFound(writer, request)
		return
	}
	server.startedRequests.Add(1)

	var chatRequest struct {
		Model    string `json:"model"`
		Messages []any  `json:"messages"`
		Stream   bool   `json:"stream"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, maxBodyBytes))
	if err := decoder.Decode(&chatRequest); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_request", "request body must be valid JSON")
		return
	}
	if chatRequest.Model == "" || len(chatRequest.Messages) == 0 {
		writeError(writer, http.StatusBadRequest, "invalid_request", "model and messages are required")
		return
	}
	if !server.wait(request, server.config.TTFT) {
		return
	}

	if chatRequest.Stream {
		server.writeStream(writer, request, chatRequest.Model)
		return
	}
	server.writeCompletion(writer, chatRequest.Model)
}

func (server *Server) writeCompletion(writer http.ResponseWriter, model string) {
	writer.Header().Set("Content-Type", "application/json")
	payload := map[string]any{
		"id":      completionID,
		"object":  "chat.completion",
		"created": int64(0),
		"model":   model,
		"choices": []any{map[string]any{
			"index": 0,
			"message": map[string]any{
				"role":    "assistant",
				"content": strings.Join(server.config.ResponseChunks, ""),
			},
			"finish_reason": "stop",
		}},
	}
	_ = json.NewEncoder(writer).Encode(payload)
}

func (server *Server) writeStream(writer http.ResponseWriter, request *http.Request, model string) {
	flusher, ok := writer.(http.Flusher)
	if !ok {
		writeError(writer, http.StatusInternalServerError, "streaming_unsupported", "response writer cannot stream")
		return
	}

	writer.Header().Set("Content-Type", "text/event-stream")
	writer.Header().Set("Cache-Control", "no-cache")
	writer.Header().Set("Connection", "keep-alive")

	for index, content := range server.config.ResponseChunks {
		if index > 0 && !server.wait(request, server.config.ChunkInterval) {
			return
		}
		choice := map[string]any{
			"index":         0,
			"delta":         map[string]any{"content": content},
			"finish_reason": nil,
		}
		if index == 0 {
			choice["delta"] = map[string]any{"role": "assistant", "content": content}
		}
		if index == len(server.config.ResponseChunks)-1 {
			choice["finish_reason"] = "stop"
		}
		payload := map[string]any{
			"id":      completionID,
			"object":  "chat.completion.chunk",
			"created": int64(0),
			"model":   model,
			"choices": []any{choice},
		}
		encoded, err := json.Marshal(payload)
		if err != nil {
			return
		}
		if _, err := fmt.Fprintf(writer, "data: %s\n\n", encoded); err != nil {
			server.observeWriteCancellation(request)
			return
		}
		flusher.Flush()

		if server.config.FailAfterChunks > 0 && index+1 >= server.config.FailAfterChunks {
			return
		}
	}

	_, _ = fmt.Fprint(writer, "data: [DONE]\n\n")
	flusher.Flush()
}

func (server *Server) wait(request *http.Request, duration time.Duration) bool {
	if duration <= 0 {
		select {
		case <-request.Context().Done():
			server.canceledRequests.Add(1)
			return false
		default:
			return true
		}
	}

	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-request.Context().Done():
		server.canceledRequests.Add(1)
		return false
	}
}

func (server *Server) observeWriteCancellation(request *http.Request) {
	if request.Context().Err() != nil {
		server.canceledRequests.Add(1)
	}
}

func writeError(writer http.ResponseWriter, status int, code string, message string) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"error": map[string]string{"code": code, "message": message},
	})
}
