package router

import (
	"sync"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type Controller struct {
	mu          sync.RWMutex
	mode        Mode
	states      map[string]backendstate.Snapshot
	affinity    *Affinity
	maxStateAge time.Duration
}

func NewController(mode Mode, affinity *Affinity) *Controller {
	if mode != ModeLoadAware && mode != ModeShadow && mode != ModeEnforced {
		mode = ModeStatic
	}
	return &Controller{mode: mode, states: map[string]backendstate.Snapshot{}, affinity: affinity}
}

func (c *Controller) SetState(snapshot backendstate.Snapshot) error {
	if err := backendstate.Validate(snapshot); err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.states[snapshot.BackendID] = snapshot
	return nil
}

func (c *Controller) Mode() Mode { c.mu.RLock(); defer c.mu.RUnlock(); return c.mode }

func (c *Controller) SetMaxStateAge(maxAge time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.maxStateAge = maxAge
}

func (c *Controller) Decide(requestID, tenantID, cacheKey string) Decision {
	c.mu.RLock()
	mode, maxStateAge, states := c.mode, c.maxStateAge, make(map[string]backendstate.Snapshot, len(c.states))
	for id, state := range c.states {
		if maxStateAge > 0 && time.Since(time.UnixMilli(state.ObservedAtUnixMillis)) > maxStateAge {
			signals := make(map[string]backendstate.Signal, len(state.Signals))
			for name, signal := range state.Signals {
				if signal.Quality == "fresh" {
					signal.Quality = "stale"
				}
				signals[name] = signal
			}
			state.Signals = signals
		}
		states[id] = state
	}
	c.mu.RUnlock()
	if mode == ModeStatic {
		return Decision{
			RequestID: requestID, TenantID: tenantID, Mode: string(ModeStatic),
			Fallback: false, Reason: "static_round_robin", OccurredAt: time.Now().UTC(),
		}
	}
	decision := Decision{}
	if mode == ModeLoadAware {
		decision = (Evaluator{}).LoadAware(requestID, tenantID, states)
	} else {
		decision = (Evaluator{}).Shadow(requestID, tenantID, states)
	}
	decision.Mode = string(mode)
	return decision
}

func (c *Controller) PreferredIDs(requestID, tenantID string, cacheKey string) []string {
	decision := c.Decide(requestID, tenantID, cacheKey)
	if decision.Mode != string(ModeEnforced) && decision.Mode != string(ModeLoadAware) {
		return nil
	}
	if decision.Fallback || decision.Selected == "" {
		return nil
	}
	if c.affinity != nil && cacheKey != "" {
		if backend, ok := c.affinity.Get(tenantID, cacheKey, decision.OccurredAt); ok && backend == decision.Selected {
			return []string{backend}
		}
		c.affinity.Put(tenantID, cacheKey, decision.Selected, decision.OccurredAt)
	}
	return []string{decision.Selected}
}
