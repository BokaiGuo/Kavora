package router

import "sync"

type DecisionLedger struct {
	mu      sync.RWMutex
	max     int
	order   []string
	entries map[string]Decision
}

func NewDecisionLedger(max int) *DecisionLedger {
	if max <= 0 {
		max = 4096
	}
	return &DecisionLedger{max: max, entries: map[string]Decision{}}
}

func (ledger *DecisionLedger) Record(decision Decision) {
	if ledger == nil || decision.RequestID == "" {
		return
	}
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	if _, exists := ledger.entries[decision.RequestID]; !exists {
		ledger.order = append(ledger.order, decision.RequestID)
	}
	ledger.entries[decision.RequestID] = decision
	for len(ledger.order) > ledger.max {
		delete(ledger.entries, ledger.order[0])
		ledger.order = ledger.order[1:]
	}
}

func (ledger *DecisionLedger) Get(requestID string) (Decision, bool) {
	if ledger == nil {
		return Decision{}, false
	}
	ledger.mu.RLock()
	defer ledger.mu.RUnlock()
	decision, ok := ledger.entries[requestID]
	return decision, ok
}

func (ledger *DecisionLedger) UpdateActual(requestID, backendID string) {
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	decision, ok := ledger.entries[requestID]
	if !ok {
		return
	}
	decision.ActualSelected = backendID
	ledger.entries[requestID] = decision
}

func (ledger *DecisionLedger) Recent(limit int) []Decision {
	if ledger == nil {
		return nil
	}
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	ledger.mu.RLock()
	defer ledger.mu.RUnlock()
	output := make([]Decision, 0, limit)
	for index := len(ledger.order) - 1; index >= 0 && len(output) < limit; index-- {
		output = append(output, ledger.entries[ledger.order[index]])
	}
	return output
}
