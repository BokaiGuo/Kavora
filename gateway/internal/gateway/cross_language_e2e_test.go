//go:build integration

package gateway_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/fakebackend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policyclient"
)

func TestGoGatewayUsesRustPolicyOverUDS(t *testing.T) {
	policyBinary := os.Getenv("KAVORA_POLICY_BINARY")
	if policyBinary == "" {
		t.Fatal("KAVORA_POLICY_BINARY is required")
	}
	socketPath := filepath.Join(t.TempDir(), "policy.sock")
	var policyLogs bytes.Buffer
	command := exec.Command(policyBinary)
	command.Env = append(os.Environ(), "KAVORA_POLICY_SOCKET="+socketPath)
	command.Stdout = &policyLogs
	command.Stderr = &policyLogs
	if err := command.Start(); err != nil {
		t.Fatalf("start Rust policy engine: %v", err)
	}
	t.Cleanup(func() {
		if command.ProcessState == nil || !command.ProcessState.Exited() {
			_ = command.Process.Signal(syscall.SIGINT)
			_ = command.Wait()
		}
	})

	deadline := time.Now().Add(3 * time.Second)
	for {
		if info, err := os.Stat(socketPath); err == nil && info.Mode()&os.ModeSocket != 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("policy socket did not become ready: %s", policyLogs.String())
		}
		time.Sleep(10 * time.Millisecond)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	policy, err := policyclient.DialUDS(ctx, socketPath)
	if err != nil {
		t.Fatalf("connect to Rust policy engine: %v\n%s", err, policyLogs.String())
	}
	t.Cleanup(func() { policy.Close() })

	backend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"from backend"}})
	backendServer := httptest.NewServer(backend)
	t.Cleanup(backendServer.Close)
	server := newGateway(t, backendServer.URL, policy)

	allowed := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}]}`)
	if allowed.StatusCode != http.StatusOK {
		allowed.Body.Close()
		t.Fatalf("allowed status = %d, want %d", allowed.StatusCode, http.StatusOK)
	}
	allowed.Body.Close()
	streamed := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"hello"}],"stream":true}`)
	if streamed.StatusCode != http.StatusOK {
		streamed.Body.Close()
		t.Fatalf("stream status = %d, want %d", streamed.StatusCode, http.StatusOK)
	}
	streamEvents := readGatewaySSE(t, streamed.Body)
	streamed.Body.Close()
	if len(streamEvents) != 2 || streamEvents[1] != "[DONE]" {
		t.Fatalf("stream events = %q, want data and DONE", streamEvents)
	}

	piiBackend := fakebackend.New(fakebackend.Config{ResponseChunks: []string{"alice@example.com"}})
	piiBackendServer := httptest.NewServer(piiBackend)
	t.Cleanup(piiBackendServer.Close)
	piiGateway := newGateway(t, piiBackendServer.URL, policy)
	piiResponse := postChat(t, piiGateway, `{"model":"demo-model","messages":[{"role":"user","content":"safe prompt"}],"stream":true}`)
	defer piiResponse.Body.Close()
	if piiResponse.StatusCode != http.StatusForbidden {
		t.Fatalf("PII stream status = %d, want %d", piiResponse.StatusCode, http.StatusForbidden)
	}
	var piiPayload struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.NewDecoder(piiResponse.Body).Decode(&piiPayload); err != nil {
		t.Fatalf("decode PII stream response: %v", err)
	}
	if piiPayload.Error.Code != "POLICY_ERROR_CODE_PII_DETECTED" {
		t.Fatalf("PII stream code = %q", piiPayload.Error.Code)
	}
	if piiBackend.StartedRequests() != 1 {
		t.Fatalf("PII backend requests = %d, want 1", piiBackend.StartedRequests())
	}

	blocked := postChat(t, server, `{"model":"demo-model","messages":[{"role":"user","content":"alice@example.com"}]}`)
	defer blocked.Body.Close()
	if blocked.StatusCode != http.StatusForbidden {
		t.Fatalf("blocked status = %d, want %d", blocked.StatusCode, http.StatusForbidden)
	}
	var payload struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.NewDecoder(blocked.Body).Decode(&payload); err != nil {
		t.Fatalf("decode block response: %v", err)
	}
	if payload.Error.Code != "POLICY_ERROR_CODE_PII_DETECTED" {
		t.Fatalf("block code = %q", payload.Error.Code)
	}
	if backend.StartedRequests() != 2 {
		t.Fatalf("backend requests = %d, want unary and stream requests only", backend.StartedRequests())
	}
}
