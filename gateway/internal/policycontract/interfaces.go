package policycontract

import (
	"context"

	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

type StreamSession interface {
	Check(context.Context, []byte) (*policyv1.StreamCheckResponse, error)
	Close(context.Context) (*policyv1.StreamCheckResponse, error)
}

type StreamEvaluator interface {
	OpenStream(context.Context, *policyv1.EvaluateRequestRequest) (StreamSession, *policyv1.PolicyResult, error)
}
