package workloadreplay

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
)

const SchemaVersion = "kavora-workload-replay/v1"

type Signature struct {
	PromptTokens       int     `json:"prompt_tokens"`
	OutputTokens       int     `json:"output_tokens"`
	SharedPrefixHash   string  `json:"shared_prefix_hash"`
	SharedPrefixTokens int     `json:"shared_prefix_tokens"`
	TenantClass        string  `json:"tenant_class"`
	ArrivalDeltaMS     float64 `json:"arrival_delta_ms"`
	Streaming          bool    `json:"streaming"`
	Model              string  `json:"model"`
}

type Config struct {
	Backends               int     `json:"backends"`
	MinHitRatio            float64 `json:"min_hit_ratio"`
	MaxConcurrency         int     `json:"max_concurrency"`
	EvidenceQuality        string  `json:"evidence_quality"`
	TTFTSLOMS              float64 `json:"ttft_slo_ms"`
	PrefillTokensPerSecond float64 `json:"prefill_tokens_per_second"`
	DecodeTokensPerSecond  float64 `json:"decode_tokens_per_second"`
}

type Metrics struct {
	Requests         int     `json:"requests"`
	P95TTFTMS        float64 `json:"p95_ttft_ms"`
	ThroughputReqS   float64 `json:"throughput_req_s"`
	SLOViolationRate float64 `json:"slo_violation_rate"`
	CacheReuseRatio  float64 `json:"cache_reuse_ratio"`
	BackendImbalance float64 `json:"backend_imbalance_cv"`
}

type Comparison struct {
	P95TTFTPercent       float64 `json:"p95_ttft_percent"`
	ThroughputPercent    float64 `json:"throughput_percent"`
	SLOViolationsPercent float64 `json:"slo_violations_percent"`
	CacheReusePercent    float64 `json:"cache_reuse_percent"`
	ImbalancePercent     float64 `json:"imbalance_percent"`
}

type Report struct {
	SchemaVersion   string     `json:"schema_version"`
	Policy          string     `json:"policy"`
	EvidenceQuality string     `json:"evidence_quality"`
	Baseline        Metrics    `json:"baseline"`
	Candidate       Metrics    `json:"candidate"`
	Comparison      Comparison `json:"comparison"`
	Recommendation  string     `json:"recommendation"`
	ApprovalStatus  string     `json:"approval_status"`
	ClaimBoundary   string     `json:"claim_boundary"`
}

func ReadTrace(reader io.Reader) ([]Signature, error) {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64*1024), 2*1024*1024)
	trace := []Signature{}
	for line := 1; scanner.Scan(); line++ {
		decoder := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
		decoder.DisallowUnknownFields()
		var signature Signature
		if err := decoder.Decode(&signature); err != nil {
			return nil, fmt.Errorf("trace line %d: %w", line, err)
		}
		if signature.PromptTokens <= 0 || signature.OutputTokens < 0 || signature.SharedPrefixTokens < 0 || signature.SharedPrefixTokens > signature.PromptTokens {
			return nil, fmt.Errorf("trace line %d: invalid token counts", line)
		}
		if signature.SharedPrefixTokens > 0 && signature.SharedPrefixHash == "" {
			return nil, fmt.Errorf("trace line %d: shared_prefix_hash is required", line)
		}
		if signature.TenantClass == "" || signature.Model == "" || signature.ArrivalDeltaMS < 0 {
			return nil, fmt.Errorf("trace line %d: tenant_class, model, and non-negative arrival_delta_ms are required", line)
		}
		trace = append(trace, signature)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(trace) == 0 {
		return nil, errors.New("trace is empty")
	}
	return trace, nil
}

func Compare(trace []Signature, config Config) (Report, error) {
	config = normalize(config)
	if err := validateConfig(config); err != nil {
		return Report{}, err
	}
	baseline := simulate(trace, config, false)
	candidate := simulate(trace, config, true)
	comparison := Comparison{
		P95TTFTPercent:       percentChange(candidate.P95TTFTMS, baseline.P95TTFTMS),
		ThroughputPercent:    percentChange(candidate.ThroughputReqS, baseline.ThroughputReqS),
		SLOViolationsPercent: percentChange(candidate.SLOViolationRate, baseline.SLOViolationRate),
		CacheReusePercent:    percentChange(candidate.CacheReuseRatio, baseline.CacheReuseRatio),
		ImbalancePercent:     percentChange(candidate.BackendImbalance, baseline.BackendImbalance),
	}
	recommendation := "NOT_SAFE_FOR_CANARY"
	if config.EvidenceQuality == "missing" || config.EvidenceQuality == "fallback" {
		recommendation = "NEEDS_MORE_EVIDENCE"
	} else if candidate.P95TTFTMS <= baseline.P95TTFTMS*1.03 && candidate.ThroughputReqS >= baseline.ThroughputReqS*.98 && candidate.SLOViolationRate <= baseline.SLOViolationRate {
		recommendation = "SAFE_FOR_CANARY"
	}
	return Report{
		SchemaVersion: SchemaVersion, Policy: "candidate", EvidenceQuality: config.EvidenceQuality,
		Baseline: baseline, Candidate: candidate, Comparison: comparison, Recommendation: recommendation,
		ApprovalStatus: "human_approval_required",
		ClaimBoundary:  "This is an offline deterministic simulation over anonymous workload signatures. It does not replay prompt content, mutate production configuration, or replace a real shadow/canary measurement.",
	}, nil
}

func normalize(config Config) Config {
	if config.Backends == 0 {
		config.Backends = 2
	}
	if config.MaxConcurrency == 0 {
		config.MaxConcurrency = 16
	}
	if config.EvidenceQuality == "" {
		config.EvidenceQuality = "missing"
	}
	if config.TTFTSLOMS == 0 {
		config.TTFTSLOMS = 500
	}
	if config.PrefillTokensPerSecond == 0 {
		config.PrefillTokensPerSecond = 8000
	}
	if config.DecodeTokensPerSecond == 0 {
		config.DecodeTokensPerSecond = 100
	}
	return config
}

func validateConfig(config Config) error {
	if config.Backends < 1 || config.MaxConcurrency < 1 || config.MinHitRatio < 0 || config.MinHitRatio > 1 || config.TTFTSLOMS <= 0 || config.PrefillTokensPerSecond <= 0 || config.DecodeTokensPerSecond <= 0 {
		return errors.New("invalid replay policy configuration")
	}
	if config.EvidenceQuality != "strict" && config.EvidenceQuality != "estimated" && config.EvidenceQuality != "fallback" && config.EvidenceQuality != "missing" {
		return errors.New("evidence_quality must be strict, estimated, fallback, or missing")
	}
	return nil
}

func simulate(trace []Signature, config Config, candidatePolicy bool) Metrics {
	backendAvailable := make([]float64, config.Backends)
	routeCounts := make([]float64, config.Backends)
	prefixOwner := map[string]int{}
	ttfts := make([]float64, 0, len(trace))
	sourceArrival := 0.0
	finish := 0.0
	activeFinishes := []float64{}
	sharedTokens := 0
	reusedTokens := 0
	violations := 0
	for index, signature := range trace {
		sourceArrival += signature.ArrivalDeltaMS
		admission := sourceArrival
		activeFinishes = retainAfter(activeFinishes, admission)
		if len(activeFinishes) >= config.MaxConcurrency {
			sort.Float64s(activeFinishes)
			admission = activeFinishes[0]
			activeFinishes = retainAfter(activeFinishes, admission)
		}
		backend := index % config.Backends
		ratio := float64(signature.SharedPrefixTokens) / float64(signature.PromptTokens)
		hasReusablePrefix := signature.SharedPrefixTokens > 0 && signature.SharedPrefixHash != ""
		if candidatePolicy && hasReusablePrefix {
			if owner, ok := prefixOwner[signature.SharedPrefixHash]; ok && ratio >= config.MinHitRatio && config.EvidenceQuality != "missing" {
				backend = owner
			} else {
				backend = leastAvailable(backendAvailable)
			}
		}
		queueDelay := math.Max(0, backendAvailable[backend]-admission)
		cached := 0
		if owner, ok := prefixOwner[signature.SharedPrefixHash]; hasReusablePrefix && ok && owner == backend {
			cached = signature.SharedPrefixTokens
		}
		uncached := signature.PromptTokens - cached
		ttft := queueDelay + 8 + float64(uncached)/config.PrefillTokensPerSecond*1000
		completion := ttft + float64(signature.OutputTokens)/config.DecodeTokensPerSecond*1000
		backendAvailable[backend] = admission + completion
		activeFinishes = append(activeFinishes, backendAvailable[backend])
		if backendAvailable[backend] > finish {
			finish = backendAvailable[backend]
		}
		if hasReusablePrefix {
			prefixOwner[signature.SharedPrefixHash] = backend
		}
		routeCounts[backend]++
		ttfts = append(ttfts, ttft)
		sharedTokens += signature.SharedPrefixTokens
		reusedTokens += cached
		if ttft > config.TTFTSLOMS {
			violations++
		}
	}
	durationSeconds := math.Max(finish/1000, .001)
	return Metrics{
		Requests:         len(trace),
		P95TTFTMS:        percentile(ttfts, .95),
		ThroughputReqS:   float64(len(trace)) / durationSeconds,
		SLOViolationRate: float64(violations) / float64(len(trace)),
		CacheReuseRatio:  safeRatio(float64(reusedTokens), float64(sharedTokens)),
		BackendImbalance: coefficientOfVariation(routeCounts),
	}
}

func retainAfter(values []float64, timestamp float64) []float64 {
	output := values[:0]
	for _, value := range values {
		if value > timestamp {
			output = append(output, value)
		}
	}
	return output
}

func leastAvailable(values []float64) int {
	selected := 0
	for index := 1; index < len(values); index++ {
		if values[index] < values[selected] {
			selected = index
		}
	}
	return selected
}

func percentile(values []float64, percentile float64) float64 {
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

func coefficientOfVariation(values []float64) float64 {
	mean := 0.0
	for _, value := range values {
		mean += value
	}
	mean /= float64(len(values))
	if mean == 0 {
		return 0
	}
	variance := 0.0
	for _, value := range values {
		difference := value - mean
		variance += difference * difference
	}
	return math.Sqrt(variance/float64(len(values))) / mean
}

func safeRatio(numerator, denominator float64) float64 {
	if denominator == 0 {
		return 0
	}
	return numerator / denominator
}
func percentChange(value, baseline float64) float64 {
	if baseline == 0 {
		if value == 0 {
			return 0
		}
		return 100
	}
	return (value/baseline - 1) * 100
}
