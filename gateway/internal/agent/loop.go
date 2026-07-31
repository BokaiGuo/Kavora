package agent

import (
	"context"
	"errors"
	"fmt"
)

type Action struct {
	Tool  string `json:"tool"`
	Input string `json:"input"`
}
type Result struct {
	Output string `json:"output"`
	Done   bool   `json:"done"`
}
type Planner interface {
	Next(context.Context, []Result) (Action, error)
}
type Executor interface {
	Execute(context.Context, Action) (Result, error)
}
type Run struct {
	Steps   []Result `json:"steps"`
	Stopped string   `json:"stopped"`
}

func RunLoop(ctx context.Context, planner Planner, executor Executor, maxSteps int) (Run, error) {
	if planner == nil || executor == nil {
		return Run{}, errors.New("planner and executor are required")
	}
	if maxSteps <= 0 {
		return Run{}, errors.New("maxSteps must be positive")
	}
	run := Run{}
	for len(run.Steps) < maxSteps {
		action, err := planner.Next(ctx, run.Steps)
		if err != nil {
			return run, fmt.Errorf("plan step: %w", err)
		}
		result, err := executor.Execute(ctx, action)
		if err != nil {
			return run, fmt.Errorf("execute %s: %w", action.Tool, err)
		}
		run.Steps = append(run.Steps, result)
		if result.Done {
			run.Stopped = "completed"
			return run, nil
		}
	}
	run.Stopped = "step_limit"
	return run, nil
}
