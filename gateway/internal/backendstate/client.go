package backendstate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
)

const SchemaVersion = "kavora.backend-state/v1"

type Signal struct {
	Value                float64 `json:"value"`
	HasValue             bool    `json:"has_value"`
	Quality              string  `json:"quality"`
	Source               string  `json:"source"`
	ObservedAtUnixMillis int64   `json:"observed_at_unix_millis"`
	Semantics            string  `json:"semantics"`
}

type Snapshot struct {
	SchemaVersion        string            `json:"schema_version"`
	BackendID            string            `json:"backend_id"`
	Backend              string            `json:"backend"`
	Model                string            `json:"model"`
	Instance             string            `json:"instance"`
	ModelGroup           string            `json:"model_group"`
	ObservedAtUnixMillis int64             `json:"observed_at_unix_millis"`
	Signals              map[string]Signal `json:"signals"`
	SnapshotHash         string            `json:"snapshot_hash"`
}

func Decode(data []byte) (Snapshot, error) {
	var snapshot Snapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return Snapshot{}, fmt.Errorf("decode backend state: %w", err)
	}
	if err := Validate(snapshot); err != nil {
		return Snapshot{}, err
	}
	return snapshot, nil
}

func Validate(snapshot Snapshot) error {
	if snapshot.SchemaVersion != SchemaVersion {
		return fmt.Errorf("unsupported backend state schema %q", snapshot.SchemaVersion)
	}
	if snapshot.BackendID == "" || snapshot.Backend == "" || snapshot.Model == "" {
		return errors.New("backend state requires backend_id, backend, and model")
	}
	if snapshot.ObservedAtUnixMillis <= 0 {
		return errors.New("backend state requires observed_at_unix_millis")
	}
	if len(snapshot.Signals) == 0 {
		return errors.New("backend state requires signals")
	}
	for name, signal := range snapshot.Signals {
		if signal.Quality != "fresh" && signal.Quality != "stale" && signal.Quality != "missing" && signal.Quality != "invalid" {
			return fmt.Errorf("signal %q has invalid quality %q", name, signal.Quality)
		}
		if signal.ObservedAtUnixMillis <= 0 || signal.Source == "" {
			return fmt.Errorf("signal %q is missing provenance", name)
		}
		if signal.Quality == "missing" && signal.HasValue {
			return fmt.Errorf("signal %q marks missing but has a value", name)
		}
		if signal.Quality != "missing" && !signal.HasValue {
			return fmt.Errorf("signal %q has no value but is %s", name, signal.Quality)
		}
	}
	return nil
}

func Value(snapshot Snapshot, name string) (float64, bool) {
	signal, ok := snapshot.Signals[name]
	if !ok || !signal.HasValue || signal.Quality == "missing" || signal.Quality == "invalid" {
		return 0, false
	}
	return signal.Value, true
}

func CanonicalHash(snapshot Snapshot) string {
	clone := snapshot
	clone.SnapshotHash = ""
	names := make([]string, 0, len(clone.Signals))
	for name := range clone.Signals {
		names = append(names, name)
	}
	sort.Strings(names)
	ordered := struct {
		SchemaVersion        string            `json:"schema_version"`
		BackendID            string            `json:"backend_id"`
		Backend              string            `json:"backend"`
		Model                string            `json:"model"`
		Instance             string            `json:"instance"`
		ModelGroup           string            `json:"model_group"`
		ObservedAtUnixMillis int64             `json:"observed_at_unix_millis"`
		Signals              map[string]Signal `json:"signals"`
	}{clone.SchemaVersion, clone.BackendID, clone.Backend, clone.Model, clone.Instance, clone.ModelGroup, clone.ObservedAtUnixMillis, clone.Signals}
	_ = names
	data, _ := json.Marshal(ordered)
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}
