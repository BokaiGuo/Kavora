package policyclient_test

import (
	"context"
	"io"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policyclient"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
	"google.golang.org/grpc"
)

func TestClientNegotiatesCapabilitiesAndEvaluatesOverUDS(t *testing.T) {
	t.Parallel()

	socketPath := filepath.Join(t.TempDir(), "policy.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen on UDS: %v", err)
	}
	grpcServer := grpc.NewServer()
	policyv1.RegisterPolicyServiceServer(grpcServer, testPolicyServer{})
	go grpcServer.Serve(listener)
	t.Cleanup(func() {
		grpcServer.Stop()
		listener.Close()
	})

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	client, err := policyclient.DialUDS(ctx, socketPath)
	if err != nil {
		t.Fatalf("dial policy service: %v", err)
	}
	t.Cleanup(func() { client.Close() })

	result, err := client.Evaluate(ctx, &policyv1.EvaluateRequestRequest{})
	if err != nil {
		t.Fatalf("evaluate policy: %v", err)
	}
	if result.Decision != policyv1.Decision_DECISION_ALLOW {
		t.Fatalf("decision = %s, want allow", result.Decision)
	}
}

func TestClientMaintainsOrderedStreamSession(t *testing.T) {
	t.Parallel()

	socketPath := filepath.Join(t.TempDir(), "policy.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen on UDS: %v", err)
	}
	grpcServer := grpc.NewServer()
	policyv1.RegisterPolicyServiceServer(grpcServer, testPolicyServer{})
	go grpcServer.Serve(listener)
	t.Cleanup(func() {
		grpcServer.Stop()
		listener.Close()
	})

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	client, err := policyclient.DialUDS(ctx, socketPath)
	if err != nil {
		t.Fatalf("dial policy service: %v", err)
	}
	t.Cleanup(func() { client.Close() })
	request := &policyv1.EvaluateRequestRequest{
		Context: &policyv1.RequestContext{RequestId: "req-stream"},
		Request: &policyv1.ModelRequest{Model: "demo"},
	}
	session, openResult, err := client.OpenStream(ctx, request)
	if err != nil {
		t.Fatalf("open policy stream: %v", err)
	}
	if openResult.Decision != policyv1.Decision_DECISION_ALLOW {
		t.Fatalf("open decision = %s, want allow", openResult.Decision)
	}

	chunkResponse, err := session.Check(ctx, []byte("event"))
	if err != nil {
		t.Fatalf("check stream chunk: %v", err)
	}
	if chunkResponse.ReleaseBytes != 5 || chunkResponse.Sequence != 1 {
		t.Fatalf("unexpected chunk response: %+v", chunkResponse)
	}
	closeResponse, err := session.Close(ctx)
	if err != nil {
		t.Fatalf("close policy stream: %v", err)
	}
	if closeResponse.Sequence != 2 {
		t.Fatalf("close sequence = %d, want 2", closeResponse.Sequence)
	}
}

type testPolicyServer struct {
	policyv1.UnimplementedPolicyServiceServer
}

func (testPolicyServer) GetCapabilities(_ context.Context, request *policyv1.GetCapabilitiesRequest) (*policyv1.GetCapabilitiesResponse, error) {
	return &policyv1.GetCapabilitiesResponse{
		ServerProtocol: &policyv1.ProtocolVersion{Major: request.ClientProtocol.Major, Minor: 0},
		EngineVersion:  "test",
		Capabilities: []policyv1.Capability{
			policyv1.Capability_CAPABILITY_REQUEST_POLICY,
			policyv1.Capability_CAPABILITY_STREAM_POLICY,
		},
	}, nil
}

func (testPolicyServer) EvaluateRequest(_ context.Context, _ *policyv1.EvaluateRequestRequest) (*policyv1.EvaluateRequestResponse, error) {
	return &policyv1.EvaluateRequestResponse{Result: &policyv1.PolicyResult{
		Decision: policyv1.Decision_DECISION_ALLOW,
	}}, nil
}

func (testPolicyServer) CheckStream(stream grpc.BidiStreamingServer[policyv1.StreamCheckRequest, policyv1.StreamCheckResponse]) error {
	for {
		request, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		response := &policyv1.StreamCheckResponse{
			RequestId: request.RequestId,
			Sequence:  request.Sequence,
			Result:    allowResultForClientTest(),
		}
		switch payload := request.Payload.(type) {
		case *policyv1.StreamCheckRequest_Chunk:
			response.ReleaseBytes = uint64(len(payload.Chunk.Data))
		case *policyv1.StreamCheckRequest_Close:
			if err := stream.Send(response); err != nil {
				return err
			}
			return nil
		}
		if err := stream.Send(response); err != nil {
			return err
		}
	}
}

func allowResultForClientTest() *policyv1.PolicyResult {
	return &policyv1.PolicyResult{Decision: policyv1.Decision_DECISION_ALLOW}
}
