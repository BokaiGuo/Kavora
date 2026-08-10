package router

import (
	"container/list"
	"context"
	"math"
	"strings"
	"sync"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
)

type Quality string

const (
	QualityFresh   Quality = "fresh"
	QualityStale   Quality = "stale"
	QualityMissing Quality = "missing"
	QualityInvalid Quality = "invalid"
)

type CacheSource string

const (
	CacheSourceNone     CacheSource = "none"
	CacheSourceAffinity CacheSource = "affinity"
	CacheSourceShadow   CacheSource = "shadow_index"
	CacheSourceKVEvents CacheSource = "kv_events"
)

type CacheMatchRequest struct {
	RequestID    string
	TenantID     string
	CacheKey     string
	PromptTokens int
}

type CacheBackend struct {
	ID    string
	State backendstate.Snapshot
}

type CacheEvidence struct {
	MatchedTokens   int         `json:"matched_tokens"`
	MatchRatio      float64     `json:"match_ratio"`
	Source          CacheSource `json:"source"`
	ObservedAt      time.Time   `json:"observed_at,omitempty"`
	Quality         Quality     `json:"quality"`
	Confidence      float64     `json:"confidence"`
	EvidenceQuality string      `json:"evidence_quality"`
}

type CacheStateProvider interface {
	Name() CacheSource
	Match(context.Context, CacheMatchRequest, CacheBackend) CacheEvidence
}

type NoCacheProvider struct{}

func (NoCacheProvider) Name() CacheSource { return CacheSourceNone }

func (NoCacheProvider) Match(context.Context, CacheMatchRequest, CacheBackend) CacheEvidence {
	return missingCacheEvidence(CacheSourceNone)
}

type AffinityProvider struct {
	affinity   *Affinity
	confidence float64
	now        func() time.Time
}

func NewAffinityProvider(affinity *Affinity, confidence float64, now func() time.Time) *AffinityProvider {
	return &AffinityProvider{affinity: affinity, confidence: clamp01(confidence), now: timeSource(now)}
}

func (*AffinityProvider) Name() CacheSource { return CacheSourceAffinity }

func (p *AffinityProvider) Match(_ context.Context, request CacheMatchRequest, backend CacheBackend) CacheEvidence {
	if p == nil || p.affinity == nil || request.CacheKey == "" {
		return missingCacheEvidence(CacheSourceAffinity)
	}
	mappedBackend, ok := p.affinity.Get(request.TenantID, request.CacheKey, p.now())
	if !ok || mappedBackend != backend.ID {
		return missingCacheEvidence(CacheSourceAffinity)
	}
	return CacheEvidence{
		MatchedTokens:   maxInt(request.PromptTokens, 0),
		MatchRatio:      1,
		Source:          CacheSourceAffinity,
		ObservedAt:      p.now().UTC(),
		Quality:         QualityFresh,
		Confidence:      p.confidence,
		EvidenceQuality: "estimated",
	}
}

type ShadowIndexProvider struct {
	confidence float64
	maxAge     time.Duration
	now        func() time.Time
}

func NewShadowIndexProvider(confidence float64, maxAge time.Duration, now func() time.Time) *ShadowIndexProvider {
	return &ShadowIndexProvider{confidence: clamp01(confidence), maxAge: maxAge, now: timeSource(now)}
}

func (*ShadowIndexProvider) Name() CacheSource { return CacheSourceShadow }

func (p *ShadowIndexProvider) Match(_ context.Context, request CacheMatchRequest, backend CacheBackend) CacheEvidence {
	if p == nil {
		return missingCacheEvidence(CacheSourceShadow)
	}
	signal, ok := backend.State.Signals["effective_residency_perc"]
	if !ok || !signal.HasValue || signal.Quality == string(QualityMissing) || signal.Quality == string(QualityInvalid) {
		return missingCacheEvidence(CacheSourceShadow)
	}
	observedAt := time.UnixMilli(signal.ObservedAtUnixMillis).UTC()
	quality := Quality(signal.Quality)
	if quality == QualityFresh && p.maxAge > 0 && p.now().Sub(observedAt) > p.maxAge {
		quality = QualityStale
	}
	ratio := clamp01(signal.Value)
	confidence := p.confidence
	if quality != QualityFresh {
		confidence = 0
	}
	return CacheEvidence{
		MatchedTokens:   int(math.Round(float64(maxInt(request.PromptTokens, 0)) * ratio)),
		MatchRatio:      ratio,
		Source:          CacheSourceShadow,
		ObservedAt:      observedAt,
		Quality:         quality,
		Confidence:      confidence,
		EvidenceQuality: backendstate.EvidenceQualityOf(signal),
	}
}

type KVEvent struct {
	Operation     string    `json:"operation,omitempty"`
	BackendID     string    `json:"backend_id"`
	CacheKey      string    `json:"cache_key"`
	MatchedTokens int       `json:"matched_tokens"`
	TotalTokens   int       `json:"total_tokens"`
	Sequence      uint64    `json:"sequence,omitempty"`
	HasSequence   bool      `json:"has_sequence,omitempty"`
	Generation    string    `json:"generation,omitempty"`
	EngineEventID string    `json:"engine_event_id,omitempty"`
	ObservedAt    time.Time `json:"observed_at"`
	Quality       Quality   `json:"quality"`
}

type kvEventEntry struct {
	key   string
	event KVEvent
}

type KVEventProvider struct {
	mu           sync.Mutex
	max          int
	ttl          time.Duration
	lambda       float64
	now          func() time.Time
	entries      map[string]*list.Element
	order        *list.List
	generation   map[string]string
	lastSequence map[string]uint64
	hasSequence  map[string]bool
	seenEvents   map[string]struct{}
	eventOrder   []string
}

func NewKVEventProvider(max int, ttl time.Duration, lambda float64, now func() time.Time) *KVEventProvider {
	if max <= 0 {
		max = 4096
	}
	if ttl <= 0 {
		ttl = 5 * time.Minute
	}
	if lambda < 0 {
		lambda = 0
	}
	return &KVEventProvider{
		max: max, ttl: ttl, lambda: lambda, now: timeSource(now),
		entries: map[string]*list.Element{}, order: list.New(), generation: map[string]string{}, lastSequence: map[string]uint64{}, hasSequence: map[string]bool{}, seenEvents: map[string]struct{}{},
	}
}

func (*KVEventProvider) Name() CacheSource { return CacheSourceKVEvents }

func (p *KVEventProvider) Observe(event KVEvent) {
	if p == nil || event.BackendID == "" {
		return
	}
	operation := event.Operation
	if operation == "" {
		operation = "store"
	}
	if operation != "clear" && event.CacheKey == "" {
		return
	}
	if event.ObservedAt.IsZero() {
		event.ObservedAt = p.now().UTC()
	}
	if event.Quality == "" {
		event.Quality = QualityFresh
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if event.Generation != "" && p.generation[event.BackendID] != "" && p.generation[event.BackendID] != event.Generation {
		p.clearBackendLocked(event.BackendID)
		delete(p.lastSequence, event.BackendID)
		delete(p.hasSequence, event.BackendID)
	}
	if event.Generation != "" {
		p.generation[event.BackendID] = event.Generation
	}
	sequenced := event.HasSequence || event.Sequence > 0
	if event.EngineEventID != "" {
		eventKey := event.BackendID + "\x00" + event.EngineEventID
		if _, exists := p.seenEvents[eventKey]; exists {
			return
		}
		if sequenced && p.hasSequence[event.BackendID] && event.Sequence < p.lastSequence[event.BackendID] {
			return
		}
		p.seenEvents[eventKey] = struct{}{}
		p.eventOrder = append(p.eventOrder, eventKey)
		for len(p.eventOrder) > p.max*4 {
			delete(p.seenEvents, p.eventOrder[0])
			p.eventOrder = p.eventOrder[1:]
		}
	} else if sequenced && p.hasSequence[event.BackendID] && event.Sequence <= p.lastSequence[event.BackendID] {
		return
	}
	if sequenced {
		p.lastSequence[event.BackendID] = event.Sequence
		p.hasSequence[event.BackendID] = true
	}
	if operation == "clear" {
		p.clearBackendLocked(event.BackendID)
		return
	}
	key := event.BackendID + "\x00" + event.CacheKey
	if operation == "remove" {
		if element, ok := p.entries[key]; ok {
			delete(p.entries, key)
			p.order.Remove(element)
		}
		return
	}
	if element, ok := p.entries[key]; ok {
		element.Value = kvEventEntry{key: key, event: event}
		p.order.MoveToFront(element)
		return
	}
	p.entries[key] = p.order.PushFront(kvEventEntry{key: key, event: event})
	for len(p.entries) > p.max {
		oldest := p.order.Back()
		entry := oldest.Value.(kvEventEntry)
		delete(p.entries, entry.key)
		p.order.Remove(oldest)
	}
}

func (p *KVEventProvider) clearBackendLocked(backendID string) {
	prefix := backendID + "\x00"
	for key, element := range p.entries {
		if strings.HasPrefix(key, prefix) {
			delete(p.entries, key)
			p.order.Remove(element)
		}
	}
}

func (p *KVEventProvider) Match(_ context.Context, request CacheMatchRequest, backend CacheBackend) CacheEvidence {
	if p == nil || request.CacheKey == "" {
		return missingCacheEvidence(CacheSourceKVEvents)
	}
	key := backend.ID + "\x00" + request.CacheKey
	p.mu.Lock()
	element, ok := p.entries[key]
	var event KVEvent
	if ok {
		p.order.MoveToFront(element)
		event = element.Value.(kvEventEntry).event
	}
	p.mu.Unlock()
	if !ok {
		return missingCacheEvidence(CacheSourceKVEvents)
	}
	age := p.now().Sub(event.ObservedAt)
	if age < 0 {
		age = 0
	}
	quality := event.Quality
	if age > p.ttl {
		quality = QualityStale
	}
	total := event.TotalTokens
	if total <= 0 {
		total = request.PromptTokens
	}
	matched := maxInt(event.MatchedTokens, 0)
	if total > 0 && matched > total {
		matched = total
	}
	if request.PromptTokens > 0 && matched > request.PromptTokens {
		matched = request.PromptTokens
	}
	ratio := 0.0
	if total > 0 {
		ratio = clamp01(float64(matched) / float64(total))
	}
	confidence := 0.0
	if quality == QualityFresh {
		confidence = confidenceForAge(age, p.lambda)
	}
	return CacheEvidence{
		MatchedTokens:   matched,
		MatchRatio:      ratio,
		Source:          CacheSourceKVEvents,
		ObservedAt:      event.ObservedAt.UTC(),
		Quality:         quality,
		Confidence:      confidence,
		EvidenceQuality: "strict",
	}
}

func missingCacheEvidence(source CacheSource) CacheEvidence {
	return CacheEvidence{Source: source, Quality: QualityMissing, EvidenceQuality: "missing"}
}

func confidenceForAge(age time.Duration, lambda float64) float64 {
	if age < 0 {
		age = 0
	}
	return clamp01(math.Exp(-lambda * age.Seconds()))
}

func clamp01(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}

func maxInt(left, right int) int {
	if left > right {
		return left
	}
	return right
}

func timeSource(now func() time.Time) func() time.Time {
	if now != nil {
		return now
	}
	return time.Now
}
