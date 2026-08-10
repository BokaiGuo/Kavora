package router

import (
	"context"
	"fmt"
	"hash/fnv"
	"math"
	"sort"
	"sync"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type RoutingRequest struct {
	RequestID    string
	TenantID     string
	Model        string
	CacheKey     string
	PromptTokens int
	Requirements map[string]string
	TTFTSLOMS    float64
}

type BackendDescriptor struct {
	ID         string
	Attributes map[string]string
}

type TTFTPredictor struct {
	BaseMS          float64
	PromptTokenMS   float64
	CachedTokenMS   float64
	QueuePenaltyMS  float64
	PressurePenalty float64
	SLOScaleMS      float64
}

func DefaultTTFTPredictor() TTFTPredictor {
	return TTFTPredictor{BaseMS: 12, PromptTokenMS: .08, CachedTokenMS: .07, QueuePenaltyMS: 8, PressurePenalty: 45, SLOScaleMS: 25}
}

func (predictor TTFTPredictor) Predict(promptTokens, matchedTokens int, queueDepth, pressure, recentPrefillRate float64) float64 {
	uncached := maxInt(promptTokens-matchedTokens, 0)
	prefillCost := float64(uncached) * predictor.PromptTokenMS
	if recentPrefillRate > 0 {
		prefillCost = float64(uncached) / recentPrefillRate * 1000
	}
	cacheLookupCost := float64(matchedTokens) * predictor.CachedTokenMS
	return math.Max(0, predictor.BaseMS+prefillCost+cacheLookupCost+queueDepth*predictor.QueuePenaltyMS+pressure*predictor.PressurePenalty)
}

func (predictor TTFTPredictor) ViolationProbability(predicted, slo float64) float64 {
	if slo <= 0 {
		return 0
	}
	scale := predictor.SLOScaleMS
	if scale <= 0 {
		scale = 25
	}
	return 1 / (1 + math.Exp(-(predicted-slo)/scale))
}

type Controller struct {
	mu          sync.RWMutex
	mode        Mode
	states      map[string]backendstate.Snapshot
	affinity    *Affinity
	provider    CacheStateProvider
	predictor   TTFTPredictor
	ledger      *DecisionLedger
	lifecycle   *Lifecycle
	maxStateAge time.Duration
	stateLambda float64
	now         func() time.Time
}

func NewController(mode Mode, affinity *Affinity) *Controller {
	if mode != ModeLoadAware && mode != ModeShadow && mode != ModeEnforced {
		mode = ModeStatic
	}
	provider := CacheStateProvider(NoCacheProvider{})
	if affinity != nil {
		provider = NewAffinityProvider(affinity, .65, nil)
	}
	return &Controller{
		mode: mode, states: map[string]backendstate.Snapshot{}, affinity: affinity,
		provider: provider, predictor: DefaultTTFTPredictor(), ledger: NewDecisionLedger(4096),
		stateLambda: .1, now: time.Now,
	}
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
func (c *Controller) SetCacheProvider(provider CacheStateProvider) {
	if provider == nil {
		provider = NoCacheProvider{}
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.provider = provider
}
func (c *Controller) CacheProvider() CacheStateProvider {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.provider
}
func (c *Controller) SetNow(now func() time.Time) {
	if now == nil {
		now = time.Now
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = now
}
func (c *Controller) SetLifecycle(lifecycle *Lifecycle) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.lifecycle = lifecycle
}
func (c *Controller) Lifecycle() *Lifecycle   { c.mu.RLock(); defer c.mu.RUnlock(); return c.lifecycle }
func (c *Controller) Ledger() *DecisionLedger { return c.ledger }

func (c *Controller) snapshot() (Mode, time.Duration, map[string]backendstate.Snapshot, CacheStateProvider, TTFTPredictor, *Lifecycle, func() time.Time) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	states := make(map[string]backendstate.Snapshot, len(c.states))
	for id, state := range c.states {
		states[id] = state
	}
	return c.mode, c.maxStateAge, states, c.provider, c.predictor, c.lifecycle, c.now
}

func (c *Controller) Plan(ctx context.Context, request RoutingRequest, backends []BackendDescriptor) Decision {
	mode, maxStateAge, states, provider, predictor, lifecycle, nowFn := c.snapshot()
	now := nowFn().UTC()
	stage, fraction, enforced, version := string(mode), 1.0, mode == ModeEnforced || mode == ModeLoadAware, "routing-v1"
	if lifecycle != nil {
		snapshot := lifecycle.Snapshot()
		stage, fraction, version = string(snapshot.Stage), snapshot.CanaryFraction, snapshot.PolicyVersion
		enforced = lifecycle.Enforces(request.RequestID)
		if snapshot.Stage == StageStatic {
			mode = ModeStatic
		} else if snapshot.Stage == StageShadow {
			mode = ModeShadow
		} else {
			mode = ModeEnforced
		}
	}
	requirements := cloneMap(request.Requirements)
	if requirements == nil {
		requirements = map[string]string{}
	}
	if request.Model != "" {
		requirements["model"] = request.Model
	}
	if request.TenantID != "" {
		requirements["tenant"] = request.TenantID
	}
	decision := Decision{
		RequestID: request.RequestID, TenantID: request.TenantID, CacheKey: request.CacheKey, PolicyVersion: version, Mode: string(mode), Stage: stage,
		Enforced: enforced, CanaryFraction: fraction, Requirements: requirements, Fallback: true,
		Reason: "no_usable_backend_state", OccurredAt: now,
	}
	for _, backend := range backends {
		candidate := Candidate{BackendID: backend.ID, Eligible: true, CacheSource: CacheSourceNone, CacheQuality: QualityMissing, Reason: "eligible"}
		for key, required := range request.Requirements {
			if backend.Attributes[key] != required {
				candidate.Eligible = false
				candidate.ExcludedBy = append(candidate.ExcludedBy, fmt.Sprintf("%s=%s", key, required))
			}
		}
		sort.Strings(candidate.ExcludedBy)
		if !candidate.Eligible {
			candidate.Reason = "hard_constraint_mismatch"
			decision.Candidates = append(decision.Candidates, candidate)
			continue
		}
		state := states[backend.ID]
		stateConfidence := 0.0
		if state.ObservedAtUnixMillis > 0 {
			age := now.Sub(time.UnixMilli(state.ObservedAtUnixMillis))
			if age < 0 {
				age = 0
			}
			if maxStateAge <= 0 || age <= maxStateAge {
				stateConfidence = confidenceForAge(age, c.stateLambda)
			}
		}
		candidate.StateConfidence = stateConfidence
		candidate.QueueDepth = signalValue(state, "queue_depth")
		candidate.KVPressure = signalValue(state, "kv_pressure")
		candidate.RecentPrefillRate = signalValue(state, "recent_prefill_tokens_per_second")
		if candidate.RecentPrefillRate == 0 {
			candidate.RecentPrefillRate = signalValue(state, "prefill_tokens_per_second")
		}
		if candidate.KVPressure == 0 {
			if coldFree, ok := backendstate.Value(state, "cold_free_perc"); ok {
				candidate.KVPressure = 1 - clamp01(coldFree)
			}
		}
		evidence := provider.Match(ctx, CacheMatchRequest{RequestID: request.RequestID, TenantID: request.TenantID, CacheKey: request.CacheKey, PromptTokens: request.PromptTokens}, CacheBackend{ID: backend.ID, State: state})
		if evidence.EvidenceQuality == "" {
			evidence.EvidenceQuality = "missing"
		}
		candidate.PrefixMatch, candidate.MatchedTokens = evidence.MatchRatio, evidence.MatchedTokens
		candidate.CacheSource, candidate.CacheQuality, candidate.CacheConfidence = evidence.Source, evidence.Quality, evidence.Confidence
		candidate.EvidenceQuality = evidence.EvidenceQuality
		candidate.PredictedTTFTMS = predictor.Predict(request.PromptTokens, evidence.MatchedTokens, candidate.QueueDepth, candidate.KVPressure, candidate.RecentPrefillRate)
		candidate.SLOViolationProbability = predictor.ViolationProbability(candidate.PredictedTTFTMS, request.TTFTSLOMS)
		confidence := evidence.Confidence
		if mode == ModeLoadAware {
			confidence = math.Max(stateConfidence, .01)
		}
		candidate.Score = confidence * (evidence.MatchRatio*100 - candidate.QueueDepth*8 - candidate.KVPressure*35 - candidate.SLOViolationProbability*60)
		candidate.Reason = "confidence_weighted_cache_queue_slo"
		decision.Candidates = append(decision.Candidates, candidate)
	}
	sortCandidates(decision.Candidates)
	for _, candidate := range decision.Candidates {
		if !candidate.Eligible {
			continue
		}
		if mode == ModeStatic {
			decision.Reason = "static_round_robin"
			decision.Fallback = false
			break
		}
		if mode == ModeLoadAware && candidate.StateConfidence <= 0 {
			decision.Reason = "no_usable_queue_state"
			break
		}
		if mode != ModeLoadAware && candidate.CacheConfidence <= 0 {
			decision.Reason = "no_confident_cache_evidence"
			break
		}
		decision.Selected = candidate.BackendID
		decision.Fallback = false
		decision.Reason = "highest_confidence_weighted_score"
		decision.Reasons = []string{"hard_constraints_satisfied", fmt.Sprintf("cache_match=%.3f confidence=%.3f", candidate.PrefixMatch, candidate.CacheConfidence), fmt.Sprintf("predicted_ttft_ms=%.2f", candidate.PredictedTTFTMS), candidate.Reason}
		break
	}
	if !hasEligible(decision.Candidates) {
		decision.Reason = "no_eligible_backend"
	}
	c.ledger.Record(decision)
	return decision
}

func (c *Controller) Decide(requestID, tenantID, cacheKey string) Decision {
	mode, maxStateAge, states, _, _, _, nowFn := c.snapshot()
	now := nowFn()
	for id, state := range states {
		if maxStateAge > 0 && now.Sub(time.UnixMilli(state.ObservedAtUnixMillis)) > maxStateAge {
			for name, signal := range state.Signals {
				if signal.Quality == string(QualityFresh) {
					signal.Quality = string(QualityStale)
					state.Signals[name] = signal
				}
			}
			states[id] = state
		}
	}
	if mode == ModeStatic {
		decision := Decision{RequestID: requestID, TenantID: tenantID, PolicyVersion: "routing-v1", Mode: string(mode), Stage: string(mode), Fallback: false, Reason: "static_round_robin", OccurredAt: now.UTC()}
		c.ledger.Record(decision)
		return decision
	}
	evaluator := Evaluator{Now: nowFn}
	var decision Decision
	if mode == ModeLoadAware {
		decision = evaluator.LoadAware(requestID, tenantID, states)
	} else {
		decision = evaluator.Shadow(requestID, tenantID, states)
	}
	decision.Mode, decision.Stage, decision.PolicyVersion = string(mode), string(mode), "routing-v1"
	decision.Enforced = mode == ModeEnforced || mode == ModeLoadAware
	c.ledger.Record(decision)
	return decision
}

func (c *Controller) PreferredIDs(requestID, tenantID string, cacheKey string) []string {
	decision := c.Decide(requestID, tenantID, cacheKey)
	return c.PreferredIDsForDecision(decision, cacheKey)
}

func (c *Controller) PreferredIDsForDecision(decision Decision, cacheKey string) []string {
	if !decision.Enforced || decision.Fallback || decision.Selected == "" {
		return nil
	}
	if c.affinity != nil && cacheKey != "" {
		c.affinity.Put(decision.TenantID, cacheKey, decision.Selected, decision.OccurredAt)
	}
	return []string{decision.Selected}
}

func (c *Controller) RecordActual(requestID, backendID string) {
	if decision, ok := c.ledger.Get(requestID); ok && c.affinity != nil && decision.CacheKey != "" {
		c.affinity.Put(decision.TenantID, decision.CacheKey, backendID, decision.OccurredAt)
	}
	c.ledger.UpdateActual(requestID, backendID)
}
func (c *Controller) ObserveKVEvent(event KVEvent) bool {
	provider, ok := c.CacheProvider().(*KVEventProvider)
	if !ok {
		return false
	}
	provider.Observe(event)
	return true
}

func signalValue(snapshot backendstate.Snapshot, name string) float64 {
	value, ok := backendstate.Value(snapshot, name)
	if !ok || snapshot.Signals[name].Quality != string(QualityFresh) {
		return 0
	}
	return value
}
func cloneMap(input map[string]string) map[string]string {
	if len(input) == 0 {
		return nil
	}
	output := make(map[string]string, len(input))
	for k, v := range input {
		output[k] = v
	}
	return output
}
func hasEligible(candidates []Candidate) bool {
	for _, candidate := range candidates {
		if candidate.Eligible {
			return true
		}
	}
	return false
}
func stableSample(requestID string) float64 {
	hash := fnv.New64a()
	_, _ = hash.Write([]byte(requestID))
	return float64(hash.Sum64()%1000000) / 1000000
}
