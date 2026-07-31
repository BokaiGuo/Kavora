package agent

import (
	"context"
	"testing"
)

type planner struct{}

func (planner) Next(context.Context, []Result) (Action, error) {
	return Action{Tool: "echo", Input: "ok"}, nil
}

type executor struct{}

func (executor) Execute(context.Context, Action) (Result, error) {
	return Result{Output: "ok", Done: true}, nil
}
func TestRunLoopStopsDeterministically(t *testing.T) {
	run, err := RunLoop(context.Background(), planner{}, executor{}, 3)
	if err != nil || run.Stopped != "completed" || len(run.Steps) != 1 {
		t.Fatalf("run=%+v err=%v", run, err)
	}
}
