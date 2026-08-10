package tenant

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"strings"

	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
	"gopkg.in/yaml.v3"
)

type Tenant struct {
	ID                  string
	MaxConcurrent       int
	TokenBudget         uint64
	PolicyFailMode      policyv1.FailMode
	RoutingRequirements map[string]string
	TTFTSLOMS           float64
}

type fileConfig struct {
	Tenants  []tenantConfig `yaml:"tenants"`
	Backends yaml.Node      `yaml:"backends"`
}

type tenantConfig struct {
	ID                  string            `yaml:"id"`
	APIKeys             []string          `yaml:"api_keys"`
	MaxConcurrent       int               `yaml:"max_concurrent"`
	TokenBudget         uint64            `yaml:"token_budget"`
	PolicyFailMode      string            `yaml:"policy_fail_mode"`
	RoutingRequirements map[string]string `yaml:"routing_requirements"`
	TTFTSLOMS           float64           `yaml:"ttft_slo_ms"`
}

type Registry struct {
	byAPIKey map[[sha256.Size]byte]Tenant
	tenants  []Tenant
}

func Load(reader io.Reader) (*Registry, error) {
	decoder := yaml.NewDecoder(reader)
	decoder.KnownFields(true)
	var config fileConfig
	if err := decoder.Decode(&config); err != nil {
		return nil, fmt.Errorf("decode tenant config: %w", err)
	}
	if len(config.Tenants) == 0 {
		return nil, errors.New("tenant config must define at least one tenant")
	}

	registry := &Registry{byAPIKey: make(map[[sha256.Size]byte]Tenant, len(config.Tenants)), tenants: make([]Tenant, 0, len(config.Tenants))}
	seenIDs := make(map[string]struct{}, len(config.Tenants))
	for index, entry := range config.Tenants {
		tenant, err := validateTenant(entry)
		if err != nil {
			return nil, fmt.Errorf("tenant %d: %w", index, err)
		}
		if _, exists := seenIDs[tenant.ID]; exists {
			return nil, fmt.Errorf("duplicate tenant ID %q", tenant.ID)
		}
		seenIDs[tenant.ID] = struct{}{}
		for _, apiKey := range entry.APIKeys {
			digest := sha256.Sum256([]byte(apiKey))
			if _, exists := registry.byAPIKey[digest]; exists {
				return nil, errors.New("duplicate API key")
			}
			registry.byAPIKey[digest] = tenant
		}
		registry.tenants = append(registry.tenants, tenant)
	}
	return registry, nil
}

func validateTenant(config tenantConfig) (Tenant, error) {
	config.ID = strings.TrimSpace(config.ID)
	if config.ID == "" {
		return Tenant{}, errors.New("id is required")
	}
	if len(config.APIKeys) == 0 {
		return Tenant{}, errors.New("at least one API key is required")
	}
	for _, apiKey := range config.APIKeys {
		if strings.TrimSpace(apiKey) == "" {
			return Tenant{}, errors.New("API keys must not be empty")
		}
	}
	if config.MaxConcurrent <= 0 {
		return Tenant{}, errors.New("max_concurrent must be positive")
	}
	if config.TokenBudget == 0 {
		return Tenant{}, errors.New("token_budget must be positive")
	}
	if config.TTFTSLOMS < 0 {
		return Tenant{}, errors.New("ttft_slo_ms must not be negative")
	}
	requirements := make(map[string]string, len(config.RoutingRequirements))
	for key, value := range config.RoutingRequirements {
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if key == "" || value == "" {
			return Tenant{}, errors.New("routing_requirements must not contain empty keys or values")
		}
		requirements[key] = value
	}

	var failMode policyv1.FailMode
	switch strings.ToLower(strings.TrimSpace(config.PolicyFailMode)) {
	case "open":
		failMode = policyv1.FailMode_FAIL_MODE_OPEN
	case "closed":
		failMode = policyv1.FailMode_FAIL_MODE_CLOSED
	default:
		return Tenant{}, errors.New("policy_fail_mode must be open or closed")
	}
	return Tenant{ID: config.ID, MaxConcurrent: config.MaxConcurrent, TokenBudget: config.TokenBudget, PolicyFailMode: failMode, RoutingRequirements: requirements, TTFTSLOMS: config.TTFTSLOMS}, nil
}

func (registry *Registry) Authenticate(apiKey string) (Tenant, bool) {
	if registry == nil || apiKey == "" {
		return Tenant{}, false
	}
	tenant, ok := registry.byAPIKey[sha256.Sum256([]byte(apiKey))]
	return tenant, ok
}

func (registry *Registry) Tenants() []Tenant {
	if registry == nil {
		return nil
	}
	return append([]Tenant(nil), registry.tenants...)
}
