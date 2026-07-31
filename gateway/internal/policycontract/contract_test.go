package policycontract_test

import (
	"encoding/hex"
	"os"
	"strings"
	"testing"

	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
	"google.golang.org/protobuf/proto"
)

func TestEvaluateRequestMatchesGoldenWireFormat(t *testing.T) {
	t.Parallel()

	wantHex, err := os.ReadFile("../../../proto/testdata/evaluate_request.hex")
	if err != nil {
		t.Fatalf("read golden fixture: %v", err)
	}
	want, err := hex.DecodeString(strings.TrimSpace(string(wantHex)))
	if err != nil {
		t.Fatalf("decode golden fixture: %v", err)
	}

	message := &policyv1.EvaluateRequestRequest{
		Context: &policyv1.RequestContext{
			RequestId:          "req-123",
			TenantId:           "tenant-a",
			PolicyVersion:      "policy-v1",
			FailMode:           policyv1.FailMode_FAIL_MODE_CLOSED,
			DeadlineUnixMillis: 42,
			MaxRequestBytes:    1024,
			MaxResponseBytes:   2048,
			TokenBudget:        128,
		},
		Request: &policyv1.ModelRequest{
			Model: "demo-model",
			Messages: []*policyv1.ChatMessage{{
				Role:        "user",
				ContentJson: []byte(`"hello"`),
			}},
			ToolsJson:                []byte(`[]`),
			GenerationParametersJson: []byte(`{"temperature":0}`),
		},
	}

	got, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	if !proto.Equal(message, decodeRequest(t, got)) {
		t.Fatal("request did not survive protobuf round trip")
	}
	if string(got) != string(want) {
		t.Fatalf("wire format changed\n got: %x\nwant: %x", got, want)
	}
}

func decodeRequest(t *testing.T, encoded []byte) *policyv1.EvaluateRequestRequest {
	t.Helper()

	decoded := &policyv1.EvaluateRequestRequest{}
	if err := proto.Unmarshal(encoded, decoded); err != nil {
		t.Fatalf("unmarshal request: %v", err)
	}
	return decoded
}
