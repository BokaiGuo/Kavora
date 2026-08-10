package router

import (
	"path/filepath"
	"testing"
	"time"
)

func TestDecisionLedgerAttachesOutcomeAndComputesPredictionError(t *testing.T) {
	ledger := NewDecisionLedger(8)
	ledger.Record(Decision{
		RequestID: "req-1",
		Selected:  "gpu-1",
		Candidates: []Candidate{{
			BackendID:       "gpu-1",
			PredictedTTFTMS: 120,
			EvidenceQuality: "strict",
		}},
	})
	ratio := .75
	matched := 750
	ledger.RecordOutcome(DecisionOutcome{
		RequestID:             "req-1",
		ActualBackend:         "gpu-1",
		TTFTMS:                150,
		E2EMS:                 420,
		Success:               true,
		StatusCode:            200,
		PromptTokens:          1000,
		OutputTokens:          64,
		ObservedCacheHitRatio: &ratio,
		ObservedMatchedTokens: &matched,
		CompletedAt:           time.Unix(100, 0).UTC(),
	})

	decision, ok := ledger.Get("req-1")
	if !ok || decision.Outcome == nil || decision.PredictionError == nil {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
	if decision.PredictionError.TTFTAbsoluteMS != 30 || decision.PredictionError.TTFTSignedMS != 30 {
		t.Fatalf("prediction_error=%+v", decision.PredictionError)
	}
	if decision.PredictionError.CacheHitAbsoluteError == nil || *decision.PredictionError.CacheHitAbsoluteError != .75 {
		t.Fatalf("prediction_error=%+v", decision.PredictionError)
	}
	if decision.Outcome.OutputTokens != 64 || decision.ActualSelected != "gpu-1" {
		t.Fatalf("decision=%+v", decision)
	}
}

func TestDecisionJournalRestoresDecisionsAndOutcomes(t *testing.T) {
	now := func() time.Time { return time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC) }
	directory := t.TempDir()
	journal, err := OpenDecisionJournal(directory, now)
	if err != nil {
		t.Fatal(err)
	}
	ledger, err := NewDecisionLedgerWithJournal(8, journal)
	if err != nil {
		t.Fatal(err)
	}
	ledger.Record(Decision{RequestID: "req-2", Selected: "gpu-0", Candidates: []Candidate{{BackendID: "gpu-0", PredictedTTFTMS: 80}}})
	ledger.RecordOutcome(DecisionOutcome{RequestID: "req-2", ActualBackend: "gpu-0", TTFTMS: 100, Success: true, StatusCode: 200, CompletedAt: now()})
	if err := ledger.Err(); err != nil {
		t.Fatal(err)
	}

	reopened, err := OpenDecisionJournal(directory, now)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := NewDecisionLedgerWithJournal(8, reopened)
	if err != nil {
		t.Fatal(err)
	}
	decision, ok := restored.Get("req-2")
	if !ok || decision.Outcome == nil || decision.PredictionError == nil {
		t.Fatalf("decision=%+v ok=%v", decision, ok)
	}
	if filepath.Base(reopened.DecisionPath()) != "decisions-2026-08-10.jsonl" || filepath.Base(reopened.OutcomePath()) != "outcomes-2026-08-10.jsonl" {
		t.Fatalf("paths=%q %q", reopened.DecisionPath(), reopened.OutcomePath())
	}
}

func TestPredictionQualitySummarizesRealizedOutcomes(t *testing.T) {
	ledger := NewDecisionLedger(8)
	for index, actual := range []float64{110, 130, 160} {
		requestID := string(rune('a' + index))
		ledger.Record(Decision{RequestID: requestID, Selected: "gpu", Candidates: []Candidate{{BackendID: "gpu", PredictedTTFTMS: 100, SLOViolationProbability: .25, EvidenceQuality: "strict"}}})
		ledger.RecordOutcome(DecisionOutcome{RequestID: requestID, ActualBackend: "gpu", TTFTMS: actual, Success: true, StatusCode: 200, CompletedAt: time.Now()})
	}
	quality := ledger.PredictionQuality(10, 120)
	if quality.Samples != 3 || quality.MAEMS != 100.0/3.0 || quality.P95AbsoluteErrorMS != 57 {
		t.Fatalf("quality=%+v", quality)
	}
	if quality.Status != "drifting" || len(quality.SLOCalibration) == 0 {
		t.Fatalf("quality=%+v", quality)
	}
}
