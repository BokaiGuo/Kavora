package router

import (
	"errors"
	"fmt"
	"sync"

	"gopkg.in/yaml.v3"
)

type LifecycleStage string

const (
	StageStatic   LifecycleStage = "static"
	StageShadow   LifecycleStage = "shadow"
	StageCanary   LifecycleStage = "canary"
	StageEnforced LifecycleStage = "enforced"
)

type LifecycleGates struct {
	MaxP95RegressionPercent float64 `yaml:"max_p95_regression_percent" json:"max_p95_regression_percent"`
	MaxErrorDelta           float64 `yaml:"max_error_delta" json:"max_error_delta"`
	MaxFallbackRate         float64 `yaml:"max_fallback_rate" json:"max_fallback_rate"`
	MaxSLOViolationRate     float64 `yaml:"max_slo_violation_rate" json:"max_slo_violation_rate"`
}

type LifecycleConfig struct {
	Enabled       bool           `yaml:"enabled" json:"enabled"`
	PolicyVersion string         `yaml:"policy_version" json:"policy_version"`
	MinRequests   int            `yaml:"min_requests" json:"min_requests"`
	CanarySteps   []float64      `yaml:"canary_steps" json:"canary_steps"`
	Gates         LifecycleGates `yaml:"gates" json:"gates"`
}

type LifecycleObservation struct {
	Requests             int     `json:"requests"`
	P95RegressionPercent float64 `json:"p95_regression_percent"`
	ErrorDelta           float64 `json:"error_delta"`
	FallbackRate         float64 `json:"fallback_rate"`
	SLOViolationRate     float64 `json:"slo_violation_rate"`
	StateHealthy         bool    `json:"state_healthy"`
	PolicyHealthy        bool    `json:"policy_healthy"`
}

type LifecycleSnapshot struct {
	Stage          LifecycleStage `json:"stage"`
	CanaryFraction float64        `json:"canary_fraction"`
	PolicyVersion  string         `json:"policy_version"`
	LastReason     string         `json:"last_reason"`
}

type Lifecycle struct {
	mu         sync.RWMutex
	config     LifecycleConfig
	stage      LifecycleStage
	step       int
	lastReason string
}

func NewLifecycle(config LifecycleConfig) (*Lifecycle, error) {
	if config.PolicyVersion == "" {
		config.PolicyVersion = "routing-v1"
	}
	if config.MinRequests <= 0 {
		config.MinRequests = 100
	}
	if len(config.CanarySteps) == 0 {
		config.CanarySteps = []float64{.05, .25, .5, 1}
	}
	previous := 0.0
	for _, step := range config.CanarySteps {
		if step <= previous || step <= 0 || step > 1 {
			return nil, errors.New("canary_steps must be strictly increasing values in (0,1]")
		}
		previous = step
	}
	stage := StageStatic
	if config.Enabled {
		stage = StageShadow
	}
	return &Lifecycle{config: config, stage: stage, lastReason: "initialized"}, nil
}

func LoadLifecycle(data []byte) (*Lifecycle, error) {
	var config LifecycleConfig
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("decode routing lifecycle: %w", err)
	}
	return NewLifecycle(config)
}

func (lifecycle *Lifecycle) Snapshot() LifecycleSnapshot {
	if lifecycle == nil {
		return LifecycleSnapshot{Stage: StageStatic, PolicyVersion: "routing-v1", LastReason: "disabled"}
	}
	lifecycle.mu.RLock()
	defer lifecycle.mu.RUnlock()
	fraction := 0.0
	if lifecycle.stage == StageCanary {
		fraction = lifecycle.config.CanarySteps[lifecycle.step]
	}
	if lifecycle.stage == StageEnforced {
		fraction = 1
	}
	return LifecycleSnapshot{Stage: lifecycle.stage, CanaryFraction: fraction, PolicyVersion: lifecycle.config.PolicyVersion, LastReason: lifecycle.lastReason}
}

func (lifecycle *Lifecycle) Enforces(requestID string) bool {
	snapshot := lifecycle.Snapshot()
	return snapshot.Stage == StageEnforced || (snapshot.Stage == StageCanary && stableSample(requestID) < snapshot.CanaryFraction)
}

func (lifecycle *Lifecycle) Observe(observation LifecycleObservation) LifecycleSnapshot {
	lifecycle.mu.Lock()
	defer lifecycle.mu.Unlock()
	if !lifecycle.config.Enabled {
		lifecycle.stage = StageStatic
		lifecycle.lastReason = "disabled"
		return lifecycle.snapshotLocked()
	}
	if !observation.StateHealthy || !observation.PolicyHealthy {
		lifecycle.stage, lifecycle.step, lifecycle.lastReason = StageStatic, 0, "unhealthy_state_or_policy"
		return lifecycle.snapshotLocked()
	}
	if observation.Requests < lifecycle.config.MinRequests {
		lifecycle.lastReason = "minimum_request_gate"
		return lifecycle.snapshotLocked()
	}
	gates := lifecycle.config.Gates
	if observation.P95RegressionPercent > gates.MaxP95RegressionPercent || observation.ErrorDelta > gates.MaxErrorDelta || observation.FallbackRate > gates.MaxFallbackRate || observation.SLOViolationRate > gates.MaxSLOViolationRate {
		lifecycle.stage, lifecycle.step, lifecycle.lastReason = StageStatic, 0, "quality_gate_failed"
		return lifecycle.snapshotLocked()
	}
	switch lifecycle.stage {
	case StageStatic:
		lifecycle.stage = StageShadow
	case StageShadow:
		lifecycle.stage, lifecycle.step = StageCanary, 0
	case StageCanary:
		if lifecycle.step+1 < len(lifecycle.config.CanarySteps) {
			lifecycle.step++
		} else {
			lifecycle.stage = StageEnforced
		}
	}
	lifecycle.lastReason = "quality_gates_passed"
	return lifecycle.snapshotLocked()
}

func (lifecycle *Lifecycle) snapshotLocked() LifecycleSnapshot {
	fraction := 0.0
	if lifecycle.stage == StageCanary {
		fraction = lifecycle.config.CanarySteps[lifecycle.step]
	}
	if lifecycle.stage == StageEnforced {
		fraction = 1
	}
	return LifecycleSnapshot{Stage: lifecycle.stage, CanaryFraction: fraction, PolicyVersion: lifecycle.config.PolicyVersion, LastReason: lifecycle.lastReason}
}
