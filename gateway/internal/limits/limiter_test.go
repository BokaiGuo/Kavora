package limits

import (
	"testing"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/tenant"
)

func TestLimiterIsolatesTenantConcurrency(t *testing.T) {
	limiter, err := New([]tenant.Tenant{
		{ID: "alpha", MaxConcurrent: 1},
		{ID: "beta", MaxConcurrent: 1},
	})
	if err != nil {
		t.Fatalf("create limiter: %v", err)
	}

	releaseAlpha, ok := limiter.TryAcquire("alpha")
	if !ok {
		t.Fatal("expected first alpha request to acquire")
	}
	if _, ok := limiter.TryAcquire("alpha"); ok {
		t.Fatal("expected second alpha request to be limited")
	}
	releaseBeta, ok := limiter.TryAcquire("beta")
	if !ok {
		t.Fatal("alpha saturation must not limit beta")
	}
	releaseBeta()
	releaseAlpha()
	if release, ok := limiter.TryAcquire("alpha"); !ok {
		t.Fatal("expected released alpha slot to be reusable")
	} else {
		release()
	}
}

func TestLimiterRejectsUnknownTenant(t *testing.T) {
	limiter, err := New([]tenant.Tenant{{ID: "alpha", MaxConcurrent: 1}})
	if err != nil {
		t.Fatalf("create limiter: %v", err)
	}
	if _, ok := limiter.TryAcquire("unknown"); ok {
		t.Fatal("unknown tenant acquired a slot")
	}
}
