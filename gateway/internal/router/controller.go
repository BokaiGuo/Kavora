package router

import (
	"sync"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type Controller struct {
	mu       sync.RWMutex
	mode     Mode
	states   map[string]backendstate.Snapshot
	affinity *Affinity
}

func NewController(mode Mode, affinity *Affinity) *Controller {
	if mode != ModeShadow && mode != ModeEnforced {
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

func (c *Controller) PreferredIDs(requestID, tenantID string, cacheKey string) []string {
	c.mu.RLock()
	mode, states := c.mode, make(map[string]backendstate.Snapshot, len(c.states))
	for id, state := range c.states {
		states[id] = state
	}
	c.mu.RUnlock()
	if mode == ModeStatic {
		return nil
	}
	decision := (Evaluator{}).Shadow(requestID, tenantID, states)
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
