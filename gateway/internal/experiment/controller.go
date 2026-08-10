package experiment

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type Arm struct {
	Policy      string   `yaml:"policy" json:"policy"`
	BackendPool []string `yaml:"backend_pool,omitempty" json:"backend_pool,omitempty"`
}

type Design struct {
	Type                 string    `yaml:"type" json:"type"`
	Window               string    `yaml:"window,omitempty" json:"window,omitempty"`
	Warmup               string    `yaml:"warmup,omitempty" json:"warmup,omitempty"`
	Cooldown             string    `yaml:"cooldown,omitempty" json:"cooldown,omitempty"`
	Seed                 string    `yaml:"seed" json:"seed"`
	TreatmentProbability float64   `yaml:"treatment_probability,omitempty" json:"treatment_probability"`
	StartAt              time.Time `yaml:"start_at,omitempty" json:"start_at,omitempty"`
}

type Guardrails struct {
	MaxErrorRate       float64 `yaml:"max_error_rate" json:"max_error_rate"`
	MaxP95TTFTMS       float64 `yaml:"max_p95_ttft_ms" json:"max_p95_ttft_ms"`
	MaxPredictionMAEMS float64 `yaml:"max_prediction_mae_ms" json:"max_prediction_mae_ms"`
	MaxFallbackRate    float64 `yaml:"max_fallback_rate" json:"max_fallback_rate"`
}

type Stop struct {
	MinRequests int    `yaml:"min_requests" json:"min_requests"`
	MaxDuration string `yaml:"max_duration" json:"max_duration"`
}

type Config struct {
	ID         string     `yaml:"id" json:"id"`
	Control    Arm        `yaml:"control" json:"control"`
	Treatment  Arm        `yaml:"treatment" json:"treatment"`
	Design     Design     `yaml:"design" json:"design"`
	Guardrails Guardrails `yaml:"guardrails" json:"guardrails"`
	Stop       Stop       `yaml:"stop" json:"stop"`
}

type Assignment struct {
	ExperimentID          string   `json:"experiment_id"`
	AssignedPolicy        string   `json:"assigned_policy"`
	AssignmentUnit        string   `json:"assignment_unit"`
	AssignmentProbability float64  `json:"assignment_probability"`
	AssignmentSeed        string   `json:"assignment_seed"`
	ExperimentWindow      string   `json:"experiment_window"`
	Warmup                bool     `json:"warmup"`
	CarryoverGuard        bool     `json:"carryover_guard"`
	BackendPool           []string `json:"backend_pool,omitempty"`
	ExperimentActive      bool     `json:"experiment_active"`
	StopReason            string   `json:"stop_reason,omitempty"`
}

type Controller struct {
	config      Config
	window      time.Duration
	warmup      time.Duration
	cooldown    time.Duration
	maxDuration time.Duration
}

func Load(data []byte) (*Controller, error) {
	var document struct {
		Experiment Config `yaml:"experiment"`
	}
	if err := yaml.Unmarshal(data, &document); err != nil {
		return nil, fmt.Errorf("decode experiment config: %w", err)
	}
	return New(document.Experiment)
}

func New(config Config) (*Controller, error) {
	if config.ID == "" || config.Control.Policy == "" || config.Treatment.Policy == "" || config.Design.Seed == "" {
		return nil, errors.New("experiment id, policies, and design seed are required")
	}
	if !validPolicy(config.Control.Policy) || !validPolicy(config.Treatment.Policy) {
		return nil, errors.New("experiment policies must be static, load-aware, kv-v1, or kv-v2")
	}
	if config.Design.TreatmentProbability == 0 {
		config.Design.TreatmentProbability = .5
	}
	if config.Design.TreatmentProbability <= 0 || config.Design.TreatmentProbability >= 1 {
		return nil, errors.New("treatment_probability must be in (0,1)")
	}
	controller := &Controller{config: config}
	var err error
	if config.Design.Type == "switchback" {
		controller.window, err = time.ParseDuration(config.Design.Window)
		if err != nil || controller.window <= 0 {
			return nil, errors.New("switchback window must be a positive duration")
		}
		controller.warmup, err = optionalDuration(config.Design.Warmup)
		if err != nil || controller.warmup < 0 {
			return nil, errors.New("warmup must be a non-negative duration")
		}
		controller.cooldown, err = optionalDuration(config.Design.Cooldown)
		if err != nil || controller.cooldown < 0 || controller.warmup+controller.cooldown >= controller.window {
			return nil, errors.New("warmup plus cooldown must be shorter than the switchback window")
		}
	} else if config.Design.Type != "isolated-pool" {
		return nil, errors.New("experiment design must be switchback or isolated-pool")
	}
	if config.Design.Type == "isolated-pool" {
		if len(config.Control.BackendPool) == 0 || len(config.Treatment.BackendPool) == 0 {
			return nil, errors.New("isolated-pool experiments require both backend pools")
		}
		seen := map[string]bool{}
		for _, backendID := range config.Control.BackendPool {
			if strings.TrimSpace(backendID) == "" || seen[backendID] {
				return nil, errors.New("isolated-pool backend IDs must be nonempty and unique")
			}
			seen[backendID] = true
		}
		treatmentSeen := map[string]bool{}
		for _, backendID := range config.Treatment.BackendPool {
			if strings.TrimSpace(backendID) == "" || treatmentSeen[backendID] {
				return nil, errors.New("isolated-pool backend IDs must be nonempty and unique")
			}
			if seen[backendID] {
				return nil, errors.New("isolated-pool backend pools must be disjoint")
			}
			treatmentSeen[backendID] = true
		}
	}
	if config.Stop.MaxDuration != "" {
		if config.Design.StartAt.IsZero() {
			return nil, errors.New("max_duration requires design.start_at")
		}
		controller.maxDuration, err = time.ParseDuration(config.Stop.MaxDuration)
		if err != nil || controller.maxDuration <= 0 {
			return nil, errors.New("max_duration must be a positive duration")
		}
	}
	return controller, nil
}

func (controller *Controller) Assign(requestID string, now time.Time) Assignment {
	probability := controller.config.Design.TreatmentProbability
	assignment := Assignment{ExperimentID: controller.config.ID, AssignmentProbability: probability, AssignmentSeed: controller.config.Design.Seed, ExperimentActive: true}
	start := controller.config.Design.StartAt
	if !start.IsZero() && now.Before(start) {
		return controller.inactiveAssignment(assignment, "not_started")
	}
	if controller.maxDuration > 0 && !start.IsZero() && now.Sub(start) >= controller.maxDuration {
		return controller.inactiveAssignment(assignment, "max_duration_reached")
	}
	useTreatment := false
	if controller.config.Design.Type == "switchback" {
		if start.IsZero() {
			start = time.Unix(0, 0).UTC()
		}
		elapsed := now.Sub(start)
		if elapsed < 0 {
			elapsed = 0
		}
		windowIndex := int64(elapsed / controller.window)
		offset := elapsed % controller.window
		assignment.AssignmentUnit = "time_window"
		assignment.ExperimentWindow = fmt.Sprintf("window-%06d", windowIndex)
		assignment.Warmup = offset < controller.warmup
		assignment.CarryoverGuard = assignment.Warmup || controller.window-offset <= controller.cooldown
		useTreatment = sample(controller.config.Design.Seed, assignment.ExperimentWindow) < probability
	} else {
		assignment.AssignmentUnit = "request"
		assignment.ExperimentWindow = "isolated-pool"
		useTreatment = sample(controller.config.Design.Seed, requestID) < probability
	}
	arm := controller.config.Control
	if useTreatment {
		arm = controller.config.Treatment
		assignment.AssignmentProbability = probability
	} else {
		assignment.AssignmentProbability = 1 - probability
	}
	assignment.AssignedPolicy = arm.Policy
	assignment.BackendPool = append([]string(nil), arm.BackendPool...)
	return assignment
}

func (controller *Controller) inactiveAssignment(assignment Assignment, reason string) Assignment {
	assignment.AssignedPolicy = controller.config.Control.Policy
	assignment.AssignmentUnit = "experiment_stop"
	assignment.AssignmentProbability = 1
	assignment.ExperimentWindow = "inactive"
	assignment.CarryoverGuard = true
	assignment.BackendPool = append([]string(nil), controller.config.Control.BackendPool...)
	assignment.ExperimentActive = false
	assignment.StopReason = reason
	return assignment
}

func (controller *Controller) Config() Config { return controller.config }

func (controller *Controller) ControlAssignment(reason string) Assignment {
	return controller.inactiveAssignment(Assignment{ExperimentID: controller.config.ID, AssignmentSeed: controller.config.Design.Seed}, reason)
}

func validPolicy(policy string) bool {
	return policy == "static" || policy == "load-aware" || policy == "kv-v1" || policy == "kv-v2"
}

func optionalDuration(value string) (time.Duration, error) {
	if value == "" {
		return 0, nil
	}
	return time.ParseDuration(value)
}

func sample(seed, unit string) float64 {
	digest := sha256.Sum256([]byte(seed + "\x00" + unit))
	return float64(binary.BigEndian.Uint64(digest[:8])) / float64(^uint64(0))
}
