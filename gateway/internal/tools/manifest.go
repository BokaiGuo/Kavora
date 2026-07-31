package tools

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
)

type Manifest struct {
	Name          string   `json:"name"`
	Version       string   `json:"version"`
	SHA256        string   `json:"sha256"`
	InputSchema   string   `json:"input_schema_json"`
	OutputSchema  string   `json:"output_schema_json"`
	Capabilities  []string `json:"capabilities"`
	TimeoutMillis uint64   `json:"timeout_millis"`
	MemoryBytes   uint64   `json:"memory_bytes"`
}

func (m Manifest) Validate() error {
	if m.Name == "" || m.Version == "" || len(m.SHA256) != 64 {
		return errors.New("tool requires name, version, and 64-character sha256")
	}
	if m.TimeoutMillis == 0 || m.MemoryBytes == 0 {
		return errors.New("tool requires positive timeout and memory limits")
	}
	for _, capability := range m.Capabilities {
		if capability == "" {
			return errors.New("tool capabilities cannot contain empty values")
		}
	}
	if m.InputSchema != "" {
		var value any
		if err := json.Unmarshal([]byte(m.InputSchema), &value); err != nil {
			return fmt.Errorf("invalid input schema: %w", err)
		}
	}
	return nil
}

func (m Manifest) CanonicalHash() string {
	capabilities := append([]string(nil), m.Capabilities...)
	sort.Strings(capabilities)
	data, _ := json.Marshal(struct {
		Name, Version, InputSchema, OutputSchema string
		SHA256                                   string
		Capabilities                             []string
		TimeoutMillis, MemoryBytes               uint64
	}{m.Name, m.Version, m.InputSchema, m.OutputSchema, m.SHA256, capabilities, m.TimeoutMillis, m.MemoryBytes})
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func Parse(data []byte) (Manifest, error) {
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, err
	}
	if err := manifest.Validate(); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}
