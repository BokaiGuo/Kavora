package backend

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"path"
	"sort"
	"strings"
	"sync"

	"gopkg.in/yaml.v3"
)

type Config struct {
	ID         string   `yaml:"id" json:"id"`
	URL        string   `yaml:"url" json:"url"`
	Enabled    *bool    `yaml:"enabled" json:"enabled"`
	Weight     int      `yaml:"weight" json:"weight"`
	Models     []string `yaml:"models" json:"models"`
	HealthPath string   `yaml:"health_path" json:"health_path"`
}

type Backend struct {
	ID         string
	URL        *url.URL
	Weight     int
	Models     map[string]struct{}
	HealthPath string
}

type Status struct {
	ID         string   `json:"id"`
	URL        string   `json:"url"`
	Enabled    bool     `json:"enabled"`
	Healthy    bool     `json:"healthy"`
	Weight     int      `json:"weight"`
	Models     []string `json:"models"`
	HealthPath string   `json:"health_path"`
}

type Registry struct {
	mu      sync.RWMutex
	entries map[string]*entry
	cursor  uint64
}

func Load(data []byte) (*Registry, error) {
	var config struct {
		Backends []Config `yaml:"backends"`
	}
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("decode backend config: %w", err)
	}
	if len(config.Backends) == 0 {
		return nil, nil
	}
	return New(config.Backends)
}

type entry struct {
	backend Backend
	enabled bool
	healthy bool
}

func New(configs []Config) (*Registry, error) {
	if len(configs) == 0 {
		return nil, errors.New("at least one backend is required")
	}
	registry := &Registry{entries: make(map[string]*entry, len(configs))}
	for index, config := range configs {
		backend, err := normalize(config)
		if err != nil {
			return nil, fmt.Errorf("backend %d: %w", index, err)
		}
		if _, exists := registry.entries[backend.ID]; exists {
			return nil, fmt.Errorf("duplicate backend ID %q", backend.ID)
		}
		enabled := true
		if config.Enabled != nil {
			enabled = *config.Enabled
		}
		registry.entries[backend.ID] = &entry{backend: backend, enabled: enabled, healthy: true}
	}
	return registry, nil
}

func normalize(config Config) (Backend, error) {
	config.ID = strings.TrimSpace(config.ID)
	if config.ID == "" {
		return Backend{}, errors.New("id is required")
	}
	parsed, err := url.Parse(config.URL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return Backend{}, errors.New("url must be an absolute HTTP URL")
	}
	if config.Weight < 0 {
		return Backend{}, errors.New("weight must not be negative")
	}
	if config.Weight == 0 {
		config.Weight = 1
	}
	models := make(map[string]struct{}, len(config.Models))
	for _, model := range config.Models {
		model = strings.TrimSpace(model)
		if model == "" {
			return Backend{}, errors.New("models must not contain empty values")
		}
		models[model] = struct{}{}
	}
	healthPath := config.HealthPath
	if healthPath == "" {
		healthPath = "/healthz"
	}
	return Backend{ID: config.ID, URL: parsed, Weight: config.Weight, Models: models, HealthPath: healthPath}, nil
}

func (registry *Registry) Candidates(model string) []Backend {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	var weighted []Backend
	target := 0
	ids := make([]string, 0, len(registry.entries))
	for id := range registry.entries {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		current := registry.entries[id]
		if !current.enabled || !current.healthy || !matches(current.backend, model) {
			continue
		}
		target++
		for index := 0; index < current.backend.Weight; index++ {
			weighted = append(weighted, current.backend)
		}
	}
	if len(weighted) == 0 {
		return nil
	}
	start := int(registry.cursor % uint64(len(weighted)))
	registry.cursor++
	ordered := make([]Backend, 0, len(weighted))
	seen := make(map[string]struct{}, len(weighted))
	for index := 0; len(ordered) < target; index++ {
		candidate := weighted[(start+index)%len(weighted)]
		if _, exists := seen[candidate.ID]; exists {
			continue
		}
		seen[candidate.ID] = struct{}{}
		ordered = append(ordered, candidate)
	}
	return ordered
}

func matches(backend Backend, model string) bool {
	if len(backend.Models) == 0 {
		return true
	}
	_, ok := backend.Models[model]
	return ok
}

func (registry *Registry) MarkFailure(id string) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if current := registry.entries[id]; current != nil {
		current.healthy = false
	}
}

func (registry *Registry) MarkSuccess(id string) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if current := registry.entries[id]; current != nil {
		current.healthy = true
	}
}

func (registry *Registry) HealthyCount() int {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	count := 0
	for _, current := range registry.entries {
		if current.enabled && current.healthy {
			count++
		}
	}
	return count
}

func (registry *Registry) Snapshot() []Status {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	statuses := make([]Status, 0, len(registry.entries))
	for _, current := range registry.entries {
		models := make([]string, 0, len(current.backend.Models))
		for model := range current.backend.Models {
			models = append(models, model)
		}
		sort.Strings(models)
		statuses = append(statuses, Status{
			ID: current.backend.ID, URL: current.backend.URL.String(), Enabled: current.enabled,
			Healthy: current.healthy, Weight: current.backend.Weight, Models: models,
			HealthPath: current.backend.HealthPath,
		})
	}
	sort.Slice(statuses, func(left, right int) bool { return statuses[left].ID < statuses[right].ID })
	return statuses
}

func (registry *Registry) CheckHealth(ctx context.Context, client *http.Client) error {
	if client == nil {
		client = http.DefaultClient
	}
	registry.mu.RLock()
	backends := make([]Backend, 0, len(registry.entries))
	for _, current := range registry.entries {
		if current.enabled {
			backends = append(backends, current.backend)
		}
	}
	registry.mu.RUnlock()
	var firstErr error
	for _, current := range backends {
		healthURL := *current.URL
		healthURL.Path = path.Join(healthURL.Path, current.HealthPath)
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL.String(), nil)
		if err != nil {
			registry.MarkFailure(current.ID)
			firstErr = err
			continue
		}
		response, err := client.Do(request)
		if err != nil || response.StatusCode < 200 || response.StatusCode >= 300 {
			if response != nil {
				_ = response.Body.Close()
			}
			registry.MarkFailure(current.ID)
			if err != nil {
				firstErr = err
			}
			continue
		}
		_ = response.Body.Close()
		registry.MarkSuccess(current.ID)
	}
	return firstErr
}
