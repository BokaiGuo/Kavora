package backendstate

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGoldenSnapshotPreservesMissing(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "proto", "testdata", "backend_state_golden.json"))
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := Decode(data)
	if err != nil {
		t.Fatal(err)
	}
	if value, ok := Value(snapshot, "cache_hit_ratio"); ok || value != 0 {
		t.Fatalf("missing cache hit ratio was interpreted as value: %v %v", value, ok)
	}
	if value, ok := Value(snapshot, "active_blocks"); !ok || value != 20 {
		t.Fatalf("active_blocks = %v %v", value, ok)
	}
}

func TestRejectsMissingAsZero(t *testing.T) {
	snapshot := Snapshot{
		SchemaVersion: SchemaVersion, BackendID: "b", Backend: "vllm", Model: "m", ObservedAtUnixMillis: 1,
		Signals: map[string]Signal{"x": {Value: 0, HasValue: true, Quality: "missing", Source: "test", ObservedAtUnixMillis: 1}},
	}
	if err := Validate(snapshot); err == nil {
		t.Fatal("expected missing-as-zero rejection")
	}
}
