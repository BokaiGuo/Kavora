package tools

import "testing"

func TestManifestValidation(t *testing.T) {
	manifest := Manifest{Name: "echo", Version: "1", SHA256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", TimeoutMillis: 100, MemoryBytes: 1024, Capabilities: []string{"time"}}
	if err := manifest.Validate(); err != nil {
		t.Fatal(err)
	}
	if manifest.CanonicalHash() == "" {
		t.Fatal("missing canonical hash")
	}
}
func TestManifestRejectsInvalidHash(t *testing.T) {
	if err := (Manifest{Name: "echo", Version: "1", SHA256: "bad", TimeoutMillis: 1, MemoryBytes: 1}).Validate(); err == nil {
		t.Fatal("expected invalid hash rejection")
	}
}
