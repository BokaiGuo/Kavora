package limits

import (
	"errors"
	"sync"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/tenant"
)

type Limiter struct {
	mu        sync.Mutex
	available map[string]int
	maximum   map[string]int
}

func New(tenants []tenant.Tenant) (*Limiter, error) {
	if len(tenants) == 0 {
		return nil, errors.New("at least one tenant is required")
	}
	limiter := &Limiter{available: make(map[string]int, len(tenants)), maximum: make(map[string]int, len(tenants))}
	for _, entry := range tenants {
		if entry.ID == "" || entry.MaxConcurrent <= 0 {
			return nil, errors.New("tenant limits require an ID and positive concurrency")
		}
		if _, exists := limiter.maximum[entry.ID]; exists {
			return nil, errors.New("duplicate tenant limit")
		}
		limiter.available[entry.ID] = entry.MaxConcurrent
		limiter.maximum[entry.ID] = entry.MaxConcurrent
	}
	return limiter, nil
}

func (limiter *Limiter) TryAcquire(tenantID string) (func(), bool) {
	limiter.mu.Lock()
	if limiter.available[tenantID] <= 0 {
		limiter.mu.Unlock()
		return nil, false
	}
	limiter.available[tenantID]--
	limiter.mu.Unlock()

	var once sync.Once
	return func() {
		once.Do(func() {
			limiter.mu.Lock()
			if limiter.available[tenantID] < limiter.maximum[tenantID] {
				limiter.available[tenantID]++
			}
			limiter.mu.Unlock()
		})
	}, true
}
