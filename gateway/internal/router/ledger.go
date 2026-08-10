package router

import (
	"bufio"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type DecisionJournal struct {
	directory string
	now       func() time.Time
}

func OpenDecisionJournal(directory string, now func() time.Time) (*DecisionJournal, error) {
	if directory == "" {
		return nil, errors.New("decision journal directory is required")
	}
	if now == nil {
		now = time.Now
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return nil, err
	}
	return &DecisionJournal{directory: directory, now: now}, nil
}

func (journal *DecisionJournal) DecisionPath() string {
	return journal.path("decisions")
}

func (journal *DecisionJournal) OutcomePath() string {
	return journal.path("outcomes")
}

func (journal *DecisionJournal) path(kind string) string {
	date := journal.now().UTC().Format("2006-01-02")
	return filepath.Join(journal.directory, kind+"-"+date+".jsonl")
}

func (journal *DecisionJournal) append(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	if _, err := file.Write(append(data, '\n')); err != nil {
		return err
	}
	return nil
}

func (journal *DecisionJournal) AppendDecision(decision Decision) error {
	return journal.append(journal.DecisionPath(), decision)
}

func (journal *DecisionJournal) AppendOutcome(outcome DecisionOutcome) error {
	return journal.append(journal.OutcomePath(), outcome)
}

func (journal *DecisionJournal) replay(kind string, target func([]byte) error) error {
	paths, err := filepath.Glob(filepath.Join(journal.directory, kind+"-*.jsonl"))
	if err != nil {
		return err
	}
	sort.Strings(paths)
	for _, path := range paths {
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		scanner := bufio.NewScanner(file)
		scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
		for scanner.Scan() {
			if err := target(append([]byte(nil), scanner.Bytes()...)); err != nil {
				file.Close()
				return err
			}
		}
		scanErr := scanner.Err()
		closeErr := file.Close()
		if scanErr != nil {
			return scanErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}

type DecisionLedger struct {
	mu      sync.RWMutex
	max     int
	order   []string
	entries map[string]Decision
	journal *DecisionJournal
	err     error
}

func NewDecisionLedger(max int) *DecisionLedger {
	ledger, _ := NewDecisionLedgerWithJournal(max, nil)
	return ledger
}

func NewDecisionLedgerWithJournal(max int, journal *DecisionJournal) (*DecisionLedger, error) {
	if max <= 0 {
		max = 4096
	}
	ledger := &DecisionLedger{max: max, entries: map[string]Decision{}, journal: journal}
	if journal == nil {
		return ledger, nil
	}
	if err := journal.replay("decisions", func(data []byte) error {
		var decision Decision
		if err := json.Unmarshal(data, &decision); err != nil {
			return err
		}
		ledger.recordLocked(decision)
		return nil
	}); err != nil {
		return nil, err
	}
	if err := journal.replay("outcomes", func(data []byte) error {
		var outcome DecisionOutcome
		if err := json.Unmarshal(data, &outcome); err != nil {
			return err
		}
		ledger.recordOutcomeLocked(outcome)
		return nil
	}); err != nil {
		return nil, err
	}
	return ledger, nil
}

func (ledger *DecisionLedger) Record(decision Decision) {
	if ledger == nil || decision.RequestID == "" {
		return
	}
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	ledger.recordLocked(decision)
	if ledger.journal != nil && ledger.err == nil {
		ledger.err = ledger.journal.AppendDecision(decision)
	}
}

func (ledger *DecisionLedger) recordLocked(decision Decision) {
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
	if ledger == nil {
		return
	}
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	decision, ok := ledger.entries[requestID]
	if !ok {
		return
	}
	decision.ActualSelected = backendID
	ledger.entries[requestID] = decision
}

func (ledger *DecisionLedger) RecordOutcome(outcome DecisionOutcome) {
	if ledger == nil || outcome.RequestID == "" {
		return
	}
	ledger.mu.Lock()
	defer ledger.mu.Unlock()
	if !ledger.recordOutcomeLocked(outcome) {
		return
	}
	if ledger.journal != nil && ledger.err == nil {
		ledger.err = ledger.journal.AppendOutcome(outcome)
	}
}

func (ledger *DecisionLedger) recordOutcomeLocked(outcome DecisionOutcome) bool {
	decision, ok := ledger.entries[outcome.RequestID]
	if !ok {
		return false
	}
	decision.ActualSelected = outcome.ActualBackend
	if outcome.CompletedAt.IsZero() && outcome.TTFTMS == 0 && outcome.E2EMS == 0 && outcome.StatusCode == 0 {
		ledger.entries[outcome.RequestID] = decision
		return true
	}
	decision.Outcome = &outcome
	if candidate, ok := candidateForBackend(decision.Candidates, outcome.ActualBackend); ok && outcome.Success && outcome.TTFTMS > 0 {
		decision.PredictorVersion = candidate.PredictorVersion
		signed := outcome.TTFTMS - candidate.PredictedTTFTMS
		decision.PredictionError = &PredictionError{
			PredictorVersion: candidate.PredictorVersion,
			PredictedTTFTMS:  candidate.PredictedTTFTMS,
			ActualTTFTMS:     outcome.TTFTMS,
			TTFTSignedMS:     signed,
			TTFTAbsoluteMS:   math.Abs(signed),
			EvidenceQuality:  candidate.EvidenceQuality,
		}
		if outcome.ObservedCacheHitRatio != nil {
			predicted := candidate.PrefixMatch
			actual := *outcome.ObservedCacheHitRatio
			absolute := math.Abs(actual - predicted)
			decision.PredictionError.PredictedCacheHitRatio = &predicted
			decision.PredictionError.ActualCacheHitRatio = &actual
			decision.PredictionError.CacheHitAbsoluteError = &absolute
		}
	}
	ledger.entries[outcome.RequestID] = decision
	return true
}

func (ledger *DecisionLedger) Err() error {
	if ledger == nil {
		return nil
	}
	ledger.mu.RLock()
	defer ledger.mu.RUnlock()
	return ledger.err
}

func (ledger *DecisionLedger) Recent(limit int) []Decision {
	if ledger == nil {
		return nil
	}
	if limit <= 0 || limit > 1000 {
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

func (ledger *DecisionLedger) PredictionQuality(limit int, sloMS float64) PredictionQuality {
	decisions := ledger.Recent(limit)
	errors := make([]float64, 0, len(decisions))
	signedTotal := 0.0
	buckets := map[string]*SLOCalibrationBucket{}
	evidence := map[string]*EvidenceQualityError{}
	for _, decision := range decisions {
		if decision.Outcome == nil || decision.PredictionError == nil {
			continue
		}
		predictionError := decision.PredictionError
		errors = append(errors, predictionError.TTFTAbsoluteMS)
		signedTotal += predictionError.TTFTSignedMS
		bucketName := probabilityBucket(predictedProbability(decision, decision.Outcome.ActualBackend))
		bucket := buckets[bucketName]
		if bucket == nil {
			bucket = &SLOCalibrationBucket{Bucket: bucketName}
			buckets[bucketName] = bucket
		}
		bucket.Samples++
		bucket.PredictedProbabilityTotal += predictedProbability(decision, decision.Outcome.ActualBackend)
		if sloMS > 0 && decision.Outcome.TTFTMS > sloMS {
			bucket.ActualViolations++
		}
		quality := predictionError.EvidenceQuality
		if quality == "" {
			quality = "missing"
		}
		group := evidence[quality]
		if group == nil {
			group = &EvidenceQualityError{EvidenceQuality: quality}
			evidence[quality] = group
		}
		group.Samples++
		group.AbsoluteErrorTotal += predictionError.TTFTAbsoluteMS
		if predictionError.CacheHitAbsoluteError != nil {
			group.CacheSamples++
			group.CacheErrorTotal += *predictionError.CacheHitAbsoluteError
		}
	}
	quality := PredictionQuality{Samples: len(errors), Status: "insufficient_evidence"}
	if len(errors) == 0 {
		return quality
	}
	for _, value := range errors {
		quality.MAEMS += value
	}
	quality.MAEMS /= float64(len(errors))
	quality.MeanSignedErrorMS = signedTotal / float64(len(errors))
	quality.P95AbsoluteErrorMS = percentileFloat(errors, .95)
	if len(errors) >= 3 {
		quality.Status = "calibrated"
		if quality.MAEMS > 25 || quality.P95AbsoluteErrorMS > 50 {
			quality.Status = "drifting"
		}
	}
	bucketNames := make([]string, 0, len(buckets))
	for name := range buckets {
		bucketNames = append(bucketNames, name)
	}
	sort.Strings(bucketNames)
	for _, name := range bucketNames {
		bucket := buckets[name]
		bucket.PredictedProbability = bucket.PredictedProbabilityTotal / float64(bucket.Samples)
		bucket.ActualViolationRate = float64(bucket.ActualViolations) / float64(bucket.Samples)
		bucket.PredictedProbabilityTotal = 0
		quality.SLOCalibration = append(quality.SLOCalibration, *bucket)
	}
	evidenceNames := make([]string, 0, len(evidence))
	for name := range evidence {
		evidenceNames = append(evidenceNames, name)
	}
	sort.Strings(evidenceNames)
	for _, name := range evidenceNames {
		group := evidence[name]
		group.MAEMS = group.AbsoluteErrorTotal / float64(group.Samples)
		if group.CacheSamples > 0 {
			group.CacheHitMAE = group.CacheErrorTotal / float64(group.CacheSamples)
		}
		group.AbsoluteErrorTotal = 0
		group.CacheErrorTotal = 0
		quality.Evidence = append(quality.Evidence, *group)
	}
	return quality
}

func candidateForBackend(candidates []Candidate, backendID string) (Candidate, bool) {
	for _, candidate := range candidates {
		if candidate.BackendID == backendID {
			return candidate, true
		}
	}
	return Candidate{}, false
}

func predictedProbability(decision Decision, backendID string) float64 {
	candidate, ok := candidateForBackend(decision.Candidates, backendID)
	if !ok {
		return 0
	}
	return candidate.SLOViolationProbability
}

func probabilityBucket(probability float64) string {
	switch {
	case probability < .2:
		return "0-20%"
	case probability < .4:
		return "20-40%"
	case probability < .6:
		return "40-60%"
	case probability < .8:
		return "60-80%"
	default:
		return "80-100%"
	}
}

func percentileFloat(values []float64, percentile float64) float64 {
	ordered := append([]float64(nil), values...)
	sort.Float64s(ordered)
	if len(ordered) == 1 {
		return ordered[0]
	}
	position := percentile * float64(len(ordered)-1)
	lower := int(math.Floor(position))
	upper := int(math.Ceil(position))
	if lower == upper {
		return ordered[lower]
	}
	return ordered[lower] + (ordered[upper]-ordered[lower])*(position-float64(lower))
}
