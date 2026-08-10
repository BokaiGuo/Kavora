package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestReplayCommandAcceptsAnonymousTraceAndReportsCanaryGate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	trace := `{"prompt_tokens":1000,"output_tokens":32,"shared_prefix_hash":"a","shared_prefix_tokens":800,"tenant_class":"research","arrival_delta_ms":0,"streaming":true,"model":"m"}` + "\n" +
		`{"prompt_tokens":1000,"output_tokens":32,"shared_prefix_hash":"b","shared_prefix_tokens":800,"tenant_class":"research","arrival_delta_ms":5,"streaming":true,"model":"m"}` + "\n" +
		`{"prompt_tokens":1000,"output_tokens":32,"shared_prefix_hash":"a","shared_prefix_tokens":800,"tenant_class":"research","arrival_delta_ms":5,"streaming":true,"model":"m"}` + "\n" +
		`{"prompt_tokens":1000,"output_tokens":32,"shared_prefix_hash":"b","shared_prefix_tokens":800,"tenant_class":"research","arrival_delta_ms":5,"streaming":true,"model":"m"}` + "\n"
	if err := os.WriteFile(path, []byte(trace), 0o600); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	code := run([]string{"--json", "replay", path, "--policy", "candidate", "--backends", "3", "--min-hit-ratio", "0.5", "--evidence-quality", "strict"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("code=%d stderr=%s", code, stderr.String())
	}
	var output map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &output); err != nil {
		t.Fatal(err)
	}
	if output["recommendation"] != "SAFE_FOR_CANARY" || output["approval_status"] != "human_approval_required" {
		t.Fatalf("output=%v", output)
	}
}
