package policyclient

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policycontract"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type Client struct {
	connection     *grpc.ClientConn
	policy         policyv1.PolicyServiceClient
	supportsStream bool
}

type StreamSession struct {
	stream    policyv1.PolicyService_CheckStreamClient
	requestID string
	sequence  uint64
	closed    bool
}

func DefaultSocketPath() (string, error) {
	if runtimeDirectory := os.Getenv("XDG_RUNTIME_DIR"); runtimeDirectory != "" {
		return filepath.Join(runtimeDirectory, "kavora", "policy.sock"), nil
	}
	homeDirectory, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(homeDirectory, ".local", "run", "kavora", "policy.sock"), nil
}

func DialUDS(ctx context.Context, socketPath string) (*Client, error) {
	if socketPath == "" {
		return nil, errors.New("policy socket path is required")
	}

	connection, err := grpc.NewClient(
		"passthrough:///kavora-policy",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		}),
	)
	if err != nil {
		return nil, err
	}
	client := &Client{
		connection: connection,
		policy:     policyv1.NewPolicyServiceClient(connection),
	}
	capabilities, err := client.policy.GetCapabilities(ctx, &policyv1.GetCapabilitiesRequest{
		ClientProtocol: &policyv1.ProtocolVersion{Major: 1, Minor: 0},
	})
	if err != nil {
		connection.Close()
		return nil, err
	}
	if capabilities.GetServerProtocol().GetMajor() != 1 || !hasRequestPolicy(capabilities.GetCapabilities()) {
		connection.Close()
		return nil, errors.New("policy service does not support Kavora request policy v1")
	}
	client.supportsStream = hasCapability(capabilities.GetCapabilities(), policyv1.Capability_CAPABILITY_STREAM_POLICY)
	return client, nil
}

func (client *Client) Evaluate(ctx context.Context, request *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error) {
	response, err := client.policy.EvaluateRequest(ctx, request)
	if err != nil {
		return nil, err
	}
	if response.Result == nil {
		return nil, errors.New("policy service returned an empty result")
	}
	return response.Result, nil
}

func (client *Client) Close() error {
	return client.connection.Close()
}

func (client *Client) OpenStream(
	ctx context.Context,
	request *policyv1.EvaluateRequestRequest,
) (policycontract.StreamSession, *policyv1.PolicyResult, error) {
	if !client.supportsStream {
		return nil, nil, errors.New("policy service does not support stream policy")
	}
	if request == nil || request.Context == nil || request.Request == nil || request.Context.RequestId == "" {
		return nil, nil, errors.New("stream policy requires request context and model request")
	}
	stream, err := client.policy.CheckStream(ctx)
	if err != nil {
		return nil, nil, err
	}
	requestID := request.Context.RequestId
	if err := stream.Send(&policyv1.StreamCheckRequest{
		RequestId: requestID,
		Sequence:  0,
		Payload: &policyv1.StreamCheckRequest_Open{Open: &policyv1.StreamOpen{
			Context: request.Context,
			Request: request.Request,
		}},
	}); err != nil {
		return nil, nil, err
	}
	response, err := stream.Recv()
	if err != nil {
		return nil, nil, err
	}
	if err := validateStreamResponse(response, requestID, 0); err != nil {
		return nil, nil, err
	}
	session := &StreamSession{stream: stream, requestID: requestID, sequence: 1}
	if response.Result.Decision != policyv1.Decision_DECISION_ALLOW {
		_ = stream.CloseSend()
		return nil, response.Result, nil
	}
	return session, response.Result, nil
}

func (session *StreamSession) Check(ctx context.Context, chunk []byte) (*policyv1.StreamCheckResponse, error) {
	if session.closed {
		return nil, errors.New("policy stream is closed")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	response, err := session.roundTrip(&policyv1.StreamCheckRequest{
		RequestId: session.requestID,
		Sequence:  session.sequence,
		Payload: &policyv1.StreamCheckRequest_Chunk{Chunk: &policyv1.StreamChunk{
			Data: append([]byte(nil), chunk...),
		}},
	})
	if err == nil {
		session.sequence++
	}
	return response, err
}

func (session *StreamSession) Close(ctx context.Context) (*policyv1.StreamCheckResponse, error) {
	if session.closed {
		return nil, errors.New("policy stream is already closed")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	response, err := session.roundTrip(&policyv1.StreamCheckRequest{
		RequestId: session.requestID,
		Sequence:  session.sequence,
		Payload:   &policyv1.StreamCheckRequest_Close{Close: &policyv1.StreamClose{}},
	})
	session.closed = true
	closeErr := session.stream.CloseSend()
	if err != nil {
		return nil, err
	}
	if closeErr != nil {
		return nil, closeErr
	}
	return response, nil
}

func (session *StreamSession) roundTrip(request *policyv1.StreamCheckRequest) (*policyv1.StreamCheckResponse, error) {
	if err := session.stream.Send(request); err != nil {
		return nil, err
	}
	response, err := session.stream.Recv()
	if err != nil {
		return nil, err
	}
	if err := validateStreamResponse(response, session.requestID, session.sequence); err != nil {
		return nil, err
	}
	return response, nil
}

func hasRequestPolicy(capabilities []policyv1.Capability) bool {
	return hasCapability(capabilities, policyv1.Capability_CAPABILITY_REQUEST_POLICY)
}

func hasCapability(capabilities []policyv1.Capability, expected policyv1.Capability) bool {
	for _, capability := range capabilities {
		if capability == expected {
			return true
		}
	}
	return false
}

func validateStreamResponse(response *policyv1.StreamCheckResponse, requestID string, sequence uint64) error {
	if response == nil || response.Result == nil {
		return errors.New("policy stream returned an empty result")
	}
	if response.RequestId != requestID || response.Sequence != sequence {
		return errors.New("policy stream returned a mismatched request ID or sequence")
	}
	return nil
}
