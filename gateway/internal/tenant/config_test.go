package tenant

import (
	"strings"
	"testing"

	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

func TestLoadBuildsAuthenticatedTenantRegistry(t *testing.T) {
	registry, err := Load(strings.NewReader(`
tenants:
  - id: team-alpha
    api_keys: [alpha-secret]
    max_concurrent: 3
    token_budget: 4096
    policy_fail_mode: closed
`))
	if err != nil {
		t.Fatalf("load tenants: %v", err)
	}

	tenant, ok := registry.Authenticate("alpha-secret")
	if !ok {
		t.Fatal("expected API key to authenticate")
	}
	if tenant.ID != "team-alpha" || tenant.MaxConcurrent != 3 || tenant.TokenBudget != 4096 {
		t.Fatalf("unexpected tenant: %+v", tenant)
	}
	if tenant.PolicyFailMode != policyv1.FailMode_FAIL_MODE_CLOSED {
		t.Fatalf("fail mode = %s", tenant.PolicyFailMode)
	}
	if _, ok := registry.Authenticate("wrong-secret"); ok {
		t.Fatal("unexpected authentication for invalid key")
	}
}

func TestLoadRejectsDuplicateAPIKeys(t *testing.T) {
	_, err := Load(strings.NewReader(`
tenants:
  - id: team-alpha
    api_keys: [shared]
    max_concurrent: 1
    token_budget: 100
    policy_fail_mode: open
  - id: team-beta
    api_keys: [shared]
    max_concurrent: 1
    token_budget: 100
    policy_fail_mode: closed
`))
	if err == nil || !strings.Contains(err.Error(), "duplicate API key") {
		t.Fatalf("error = %v, want duplicate API key", err)
	}
}

func TestLoadRejectsInvalidTenantPolicy(t *testing.T) {
	tests := []struct {
		name   string
		config string
	}{
		{name: "empty id", config: "id: ''\n    api_keys: [key]\n    max_concurrent: 1\n    token_budget: 100\n    policy_fail_mode: closed"},
		{name: "no keys", config: "id: alpha\n    api_keys: []\n    max_concurrent: 1\n    token_budget: 100\n    policy_fail_mode: closed"},
		{name: "zero concurrency", config: "id: alpha\n    api_keys: [key]\n    max_concurrent: 0\n    token_budget: 100\n    policy_fail_mode: closed"},
		{name: "zero budget", config: "id: alpha\n    api_keys: [key]\n    max_concurrent: 1\n    token_budget: 0\n    policy_fail_mode: closed"},
		{name: "invalid fail mode", config: "id: alpha\n    api_keys: [key]\n    max_concurrent: 1\n    token_budget: 100\n    policy_fail_mode: maybe"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := Load(strings.NewReader("tenants:\n  - " + test.config + "\n"))
			if err == nil {
				t.Fatal("expected invalid tenant config to fail")
			}
		})
	}
}
