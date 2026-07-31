package gateway

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/limits"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policycontract"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/telemetry"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/tenant"
	policyv1 "github.com/BokaiGuo-Lincoln/kavora/proto/gen/go/policy/v1"
)

const defaultTenantID = "default"

type PolicyEvaluator interface {
	Evaluate(context.Context, *policyv1.EvaluateRequestRequest) (*policyv1.PolicyResult, error)
}

type Config struct {
	BackendURL        string
	Policy            PolicyEvaluator
	RequestTimeout    time.Duration
	MaxRequestBytes   int64
	MaxResponseBytes  int64
	StreamChunkBytes  int
	StreamBufferBytes int
	StreamPolicy      policycontract.StreamEvaluator
	TokenBudget       uint64
	Tenants           *tenant.Registry
	Backends          *backend.Registry
	Metrics           *telemetry.Metrics
	Audit             *telemetry.AuditLogger
	Router            *router.Controller
}

type Server struct {
	backendURL        *url.URL
	policy            PolicyEvaluator
	httpClient        *http.Client
	requestTimeout    time.Duration
	maxRequestBytes   int64
	maxResponseBytes  int64
	streamChunkBytes  int
	streamBufferBytes int
	streamPolicy      policycontract.StreamEvaluator
	tokenBudget       uint64
	backends          *backend.Registry
	metrics           *telemetry.Metrics
	audit             *telemetry.AuditLogger
	router            *router.Controller
	tenants           *tenant.Registry
	limiter           *limits.Limiter
}

func New(config Config) (*Server, error) {
	if config.Policy == nil {
		return nil, errors.New("policy evaluator is required")
	}
	var backendURL *url.URL
	var err error
	if config.Backends == nil {
		backendURL, err = url.Parse(config.BackendURL)
		if err != nil || backendURL.Scheme == "" || backendURL.Host == "" {
			return nil, errors.New("backend URL must be an absolute HTTP URL")
		}
		if backendURL.Scheme != "http" && backendURL.Scheme != "https" {
			return nil, errors.New("backend URL scheme must be http or https")
		}
	}
	if config.RequestTimeout <= 0 {
		return nil, errors.New("request timeout must be positive")
	}
	if config.MaxRequestBytes <= 0 {
		return nil, errors.New("max request bytes must be positive")
	}
	if config.MaxResponseBytes <= 0 {
		return nil, errors.New("max response bytes must be positive")
	}
	if config.StreamPolicy != nil && (config.StreamChunkBytes <= 0 || config.StreamBufferBytes <= 0) {
		return nil, errors.New("stream chunk and buffer bytes must be positive")
	}
	var limiter *limits.Limiter
	if config.Tenants != nil {
		limiter, err = limits.New(config.Tenants.Tenants())
		if err != nil {
			return nil, err
		}
	}
	return &Server{
		backendURL:        backendURL,
		policy:            config.Policy,
		httpClient:        &http.Client{},
		requestTimeout:    config.RequestTimeout,
		maxRequestBytes:   config.MaxRequestBytes,
		maxResponseBytes:  config.MaxResponseBytes,
		streamChunkBytes:  config.StreamChunkBytes,
		streamBufferBytes: config.StreamBufferBytes,
		streamPolicy:      config.StreamPolicy,
		tokenBudget:       config.TokenBudget,
		backends:          config.Backends,
		metrics:           metricsOrDefault(config.Metrics),
		audit:             config.Audit,
		router:            config.Router,
		tenants:           config.Tenants,
		limiter:           limiter,
	}, nil
}

func (server *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	started := time.Now()
	observed := &observedWriter{ResponseWriter: writer}
	var requestWriter http.ResponseWriter = observed
	if _, ok := writer.(http.Flusher); ok {
		requestWriter = &observedFlusher{observedWriter: observed}
	}
	writer = requestWriter
	server.metrics.InflightAdd(1)
	tenantID := ""
	policyDecision := ""
	isStream := false
	defer func() {
		server.metrics.InflightAdd(-1)
		status := observed.status
		if status == 0 {
			status = http.StatusOK
		}
		outcome := "success"
		if status >= 500 {
			outcome = "error"
		} else if status >= 400 {
			outcome = "rejected"
		}
		server.metrics.IncRequest(request.URL.Path, outcome)
		server.metrics.ObserveRequest(request.URL.Path, outcome, time.Since(started))
		server.audit.Log(telemetry.AuditEvent{
			Event:      "request_completed",
			RequestID:  observed.Header().Get("X-Request-ID"),
			TenantID:   tenantID,
			Policy:     policyDecision,
			Outcome:    outcome,
			Stream:     isStream,
			DurationMS: time.Since(started).Milliseconds(),
		})
	}()
	if request.Method != http.MethodPost || request.URL.Path != "/v1/chat/completions" {
		http.NotFound(writer, request)
		return
	}

	requestID, err := newRequestID()
	if err != nil {
		writeGatewayError(writer, http.StatusInternalServerError, "INTERNAL", "failed to create request ID", "")
		return
	}
	writer.Header().Set("X-Request-ID", requestID)
	activeTenant := tenant.Tenant{
		ID:             defaultTenantID,
		MaxConcurrent:  1,
		TokenBudget:    server.tokenBudget,
		PolicyFailMode: policyv1.FailMode_FAIL_MODE_CLOSED,
	}
	if server.tenants != nil {
		apiKey, ok := tenant.BearerToken(request.Header.Get("Authorization"))
		if !ok {
			writer.Header().Set("WWW-Authenticate", "Bearer")
			writeGatewayError(writer, http.StatusUnauthorized, "UNAUTHORIZED", "a valid Bearer API key is required", requestID)
			return
		}
		activeTenant, ok = server.tenants.Authenticate(apiKey)
		if !ok {
			writer.Header().Set("WWW-Authenticate", "Bearer")
			writeGatewayError(writer, http.StatusUnauthorized, "UNAUTHORIZED", "a valid Bearer API key is required", requestID)
			return
		}
		tenantID = activeTenant.ID
		release, acquired := server.limiter.TryAcquire(activeTenant.ID)
		if !acquired {
			writeGatewayError(writer, http.StatusTooManyRequests, "TENANT_CONCURRENCY_LIMIT", "tenant concurrency limit exceeded", requestID)
			return
		}
		defer release()
	}

	ctx, cancel := context.WithTimeout(request.Context(), server.requestTimeout)
	defer cancel()

	rawBody, policyRequest, stream, err := server.parseRequest(writer, request, requestID, ctx, activeTenant)
	isStream = stream
	if err != nil {
		writeGatewayError(writer, http.StatusBadRequest, "INVALID_REQUEST", err.Error(), requestID)
		return
	}
	result, err := server.policy.Evaluate(ctx, policyRequest)
	if result != nil {
		policyDecision = result.Decision.String()
		server.metrics.IncPolicy(result.Decision.String())
	}
	if err != nil && activeTenant.PolicyFailMode == policyv1.FailMode_FAIL_MODE_CLOSED {
		writeGatewayError(writer, http.StatusServiceUnavailable, "POLICY_UNAVAILABLE", "policy evaluation failed", requestID)
		return
	}
	if result == nil && activeTenant.PolicyFailMode == policyv1.FailMode_FAIL_MODE_CLOSED {
		writeGatewayError(writer, http.StatusServiceUnavailable, "POLICY_UNAVAILABLE", "policy returned no result", requestID)
		return
	}
	if result != nil && result.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE && activeTenant.PolicyFailMode == policyv1.FailMode_FAIL_MODE_OPEN {
		result = nil
	}
	if result != nil && result.Decision != policyv1.Decision_DECISION_ALLOW {
		status := http.StatusForbidden
		if result.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE {
			status = http.StatusServiceUnavailable
		}
		message := "request rejected by policy"
		if result.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE {
			message = "policy evaluation is temporarily unavailable"
		}
		writeGatewayError(writer, status, result.ErrorCode.String(), message, requestID)
		return
	}

	server.forward(writer, ctx, rawBody, policyRequest, requestID, stream)
}

func metricsOrDefault(metrics *telemetry.Metrics) *telemetry.Metrics {
	if metrics == nil {
		return telemetry.NewMetrics()
	}
	return metrics
}

type observedWriter struct {
	http.ResponseWriter
	status int
}

func (writer *observedWriter) WriteHeader(status int) {
	if writer.status != 0 {
		return
	}
	writer.status = status
	writer.ResponseWriter.WriteHeader(status)
}

func (writer *observedWriter) Write(data []byte) (int, error) {
	if writer.status == 0 {
		writer.WriteHeader(http.StatusOK)
	}
	return writer.ResponseWriter.Write(data)
}

type observedFlusher struct {
	*observedWriter
}

func (writer *observedFlusher) Flush() {
	if writer.status == 0 {
		writer.WriteHeader(http.StatusOK)
	}
	if flusher, ok := writer.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (server *Server) parseRequest(
	writer http.ResponseWriter,
	request *http.Request,
	requestID string,
	ctx context.Context,
	activeTenant tenant.Tenant,
) ([]byte, *policyv1.EvaluateRequestRequest, bool, error) {
	body, err := io.ReadAll(http.MaxBytesReader(writer, request.Body, server.maxRequestBytes))
	if err != nil {
		return nil, nil, false, errors.New("request body exceeds configured limit")
	}

	var rawFields map[string]json.RawMessage
	if err := json.Unmarshal(body, &rawFields); err != nil {
		return nil, nil, false, errors.New("request body must be valid JSON")
	}
	var model string
	if err := json.Unmarshal(rawFields["model"], &model); err != nil || model == "" {
		return nil, nil, false, errors.New("model is required")
	}
	var rawMessages []struct {
		Role    string          `json:"role"`
		Content json.RawMessage `json:"content"`
		Name    *string         `json:"name"`
	}
	if err := json.Unmarshal(rawFields["messages"], &rawMessages); err != nil || len(rawMessages) == 0 {
		return nil, nil, false, errors.New("messages are required")
	}
	var stream bool
	if encoded, ok := rawFields["stream"]; ok {
		if err := json.Unmarshal(encoded, &stream); err != nil {
			return nil, nil, false, errors.New("stream must be a boolean")
		}
	}

	messages := make([]*policyv1.ChatMessage, 0, len(rawMessages))
	for _, message := range rawMessages {
		if message.Role == "" || len(message.Content) == 0 {
			return nil, nil, false, errors.New("each message requires role and content")
		}
		messages = append(messages, &policyv1.ChatMessage{
			Role:        message.Role,
			ContentJson: append([]byte(nil), message.Content...),
			Name:        message.Name,
		})
	}

	toolsJSON := []byte("[]")
	if encoded, ok := rawFields["tools"]; ok {
		toolsJSON = append([]byte(nil), encoded...)
	}
	generationFields := make(map[string]json.RawMessage, len(rawFields))
	for key, value := range rawFields {
		switch key {
		case "model", "messages", "tools", "stream":
		default:
			generationFields[key] = value
		}
	}
	generationJSON, err := json.Marshal(generationFields)
	if err != nil {
		return nil, nil, false, errors.New("generation parameters are invalid")
	}

	deadline, _ := ctx.Deadline()
	return body, &policyv1.EvaluateRequestRequest{
		Context: &policyv1.RequestContext{
			RequestId:          requestID,
			TenantId:           activeTenant.ID,
			PolicyVersion:      "policy-v1",
			FailMode:           activeTenant.PolicyFailMode,
			DeadlineUnixMillis: uint64(deadline.UnixMilli()),
			MaxRequestBytes:    uint64(server.maxRequestBytes),
			MaxResponseBytes:   uint64(server.maxResponseBytes),
			TokenBudget:        activeTenant.TokenBudget,
		},
		Request: &policyv1.ModelRequest{
			Model:                    model,
			Messages:                 messages,
			ToolsJson:                toolsJSON,
			GenerationParametersJson: generationJSON,
		},
	}, stream, nil
}

func (server *Server) forward(
	writer http.ResponseWriter,
	ctx context.Context,
	body []byte,
	policyRequest *policyv1.EvaluateRequestRequest,
	requestID string,
	stream bool,
) {
	var flusher http.Flusher
	var streamSession policycontract.StreamSession
	if stream {
		var ok bool
		flusher, ok = writer.(http.Flusher)
		if !ok {
			writeGatewayError(writer, http.StatusInternalServerError, "STREAMING_UNSUPPORTED", "response writer cannot stream", requestID)
			return
		}
		if server.streamPolicy == nil {
			if policyRequest.Context.FailMode == policyv1.FailMode_FAIL_MODE_CLOSED {
				writeGatewayError(writer, http.StatusServiceUnavailable, "STREAM_POLICY_UNAVAILABLE", "stream policy is unavailable", requestID)
				return
			}
		} else {
			var openResult *policyv1.PolicyResult
			var err error
			streamSession, openResult, err = server.streamPolicy.OpenStream(ctx, policyRequest)
			if err != nil || openResult == nil || openResult.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE {
				if policyRequest.Context.FailMode == policyv1.FailMode_FAIL_MODE_CLOSED {
					writeGatewayError(writer, http.StatusServiceUnavailable, "STREAM_POLICY_UNAVAILABLE", "failed to open stream policy", requestID)
					return
				}
				streamSession = nil
			} else if openResult.Decision != policyv1.Decision_DECISION_ALLOW {
				writeGatewayError(writer, http.StatusForbidden, "STREAM_POLICY_REJECTED", "stream rejected by policy", requestID)
				return
			}
		}
	}

	candidates := server.backendCandidates(policyRequest.Request.Model)
	if len(candidates) == 0 {
		writeGatewayError(writer, http.StatusServiceUnavailable, "BACKEND_UNAVAILABLE", "no healthy backend supports the requested model", requestID)
		return
	}
	for _, candidate := range candidates {
		server.metrics.IncBackend(candidate.ID, "attempt")
		backendURL := candidate.URL.ResolveReference(&url.URL{Path: "/v1/chat/completions"})
		backendRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, backendURL.String(), bytes.NewReader(body))
		if err != nil {
			server.metrics.IncBackend(candidate.ID, "failure")
			if server.backends != nil {
				server.backends.MarkFailure(candidate.ID)
			}
			continue
		}
		backendRequest.Header.Set("Content-Type", "application/json")
		backendRequest.Header.Set("X-Request-ID", requestID)
		response, err := server.httpClient.Do(backendRequest)
		if err != nil {
			server.metrics.IncBackend(candidate.ID, "failure")
			if server.backends != nil {
				server.backends.MarkFailure(candidate.ID)
			}
			continue
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			server.metrics.IncBackend(candidate.ID, "failure")
			_ = response.Body.Close()
			if server.backends != nil {
				server.backends.MarkFailure(candidate.ID)
			}
			continue
		}
		if server.backends != nil {
			server.backends.MarkSuccess(candidate.ID)
		}
		server.metrics.IncBackend(candidate.ID, "success")
		if stream {
			server.forwardStream(writer, response, flusher, streamSession, requestID, policyRequest.Context.FailMode)
		} else {
			server.forwardBuffered(writer, response, requestID)
		}
		_ = response.Body.Close()
		return
	}
	writeGatewayError(writer, http.StatusBadGateway, "BACKEND_UNAVAILABLE", "all candidate backends failed before response", requestID)
}

func (server *Server) backendCandidates(model string) []backend.Backend {
	var candidates []backend.Backend
	if server.backends != nil {
		candidates = server.backends.Candidates(model)
	} else {
		candidates = []backend.Backend{{ID: "default", URL: server.backendURL}}
	}
	if server.router == nil {
		return candidates
	}
	preferred := server.router.PreferredIDs("", defaultTenantID, model)
	if len(preferred) == 0 {
		return candidates
	}
	ordered := make([]backend.Backend, 0, len(candidates))
	used := map[string]bool{}
	for _, id := range preferred {
		for _, candidate := range candidates {
			if candidate.ID == id {
				ordered = append(ordered, candidate)
				used[id] = true
			}
		}
	}
	for _, candidate := range candidates {
		if !used[candidate.ID] {
			ordered = append(ordered, candidate)
		}
	}
	return ordered
}

func (server *Server) forwardStream(
	writer http.ResponseWriter,
	response *http.Response,
	flusher http.Flusher,
	session policycontract.StreamSession,
	requestID string,
	failMode policyv1.FailMode,
) {
	copyBackendHeaders(writer.Header(), response.Header)
	if session == nil {
		writer.WriteHeader(response.StatusCode)
		_, _ = io.Copy(writer, response.Body)
		flusher.Flush()
		return
	}
	buffer := make([]byte, server.streamChunkBytes)
	pending := make([]byte, 0, server.streamBufferBytes+server.streamChunkBytes)
	responseStarted := false
	for {
		bytesRead, readErr := response.Body.Read(buffer)
		if bytesRead > 0 {
			pending = append(pending, buffer[:bytesRead]...)
			decision, policyErr := session.Check(response.Request.Context(), buffer[:bytesRead])
			if policyErr != nil || decision == nil || decision.Result == nil || decision.Result.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE {
				if failMode == policyv1.FailMode_FAIL_MODE_OPEN {
					if !responseStarted {
						writer.WriteHeader(response.StatusCode)
					}
					_, _ = writer.Write(pending)
					_, _ = io.Copy(writer, response.Body)
					flusher.Flush()
					return
				}
				if !responseStarted {
					writeGatewayError(writer, http.StatusServiceUnavailable, "STREAM_POLICY_UNAVAILABLE", "stream policy failed", requestID)
				}
				return
			}
			if decision.Result.Decision != policyv1.Decision_DECISION_ALLOW {
				if !responseStarted {
					writeGatewayError(writer, http.StatusForbidden, decision.Result.ErrorCode.String(), "stream terminated by policy", requestID)
				}
				return
			}
			if decision.ReleaseBytes > uint64(len(pending)) {
				if !responseStarted {
					writeGatewayError(writer, http.StatusBadGateway, "STREAM_POLICY_PROTOCOL_ERROR", "invalid stream release count", requestID)
				}
				return
			}
			if decision.ReleaseBytes > 0 {
				if !responseStarted {
					writer.WriteHeader(response.StatusCode)
					responseStarted = true
				}
				releaseBytes := int(decision.ReleaseBytes)
				if _, writeErr := writer.Write(pending[:releaseBytes]); writeErr != nil {
					return
				}
				pending = append(pending[:0], pending[releaseBytes:]...)
				flusher.Flush()
			}
			if len(pending) > server.streamBufferBytes {
				if !responseStarted {
					writeGatewayError(writer, http.StatusBadGateway, "STREAM_POLICY_BUFFER_FULL", "stream policy buffer limit exceeded", requestID)
				}
				return
			}
		}
		if readErr != nil {
			break
		}
	}
	closeDecision, err := session.Close(response.Request.Context())
	if err != nil || closeDecision == nil || closeDecision.Result == nil || closeDecision.Result.Decision == policyv1.Decision_DECISION_RETRYABLE_FAILURE {
		if failMode == policyv1.FailMode_FAIL_MODE_OPEN {
			if !responseStarted {
				writer.WriteHeader(response.StatusCode)
			}
			_, _ = writer.Write(pending)
			flusher.Flush()
			return
		}
		if !responseStarted {
			writeGatewayError(writer, http.StatusBadGateway, "STREAM_POLICY_INCOMPLETE", "stream did not close cleanly", requestID)
		}
		return
	}
	if closeDecision.Result.Decision != policyv1.Decision_DECISION_ALLOW {
		if !responseStarted {
			writeGatewayError(writer, http.StatusForbidden, closeDecision.Result.ErrorCode.String(), "stream terminated by policy", requestID)
		}
		return
	}
	if closeDecision.ReleaseBytes > uint64(len(pending)) {
		if !responseStarted {
			writeGatewayError(writer, http.StatusBadGateway, "STREAM_POLICY_PROTOCOL_ERROR", "invalid close release count", requestID)
		}
		return
	}
	if closeDecision.ReleaseBytes > 0 {
		if !responseStarted {
			writer.WriteHeader(response.StatusCode)
			responseStarted = true
		}
		releaseBytes := int(closeDecision.ReleaseBytes)
		_, _ = writer.Write(pending[:releaseBytes])
		pending = pending[releaseBytes:]
		flusher.Flush()
	}
	if len(pending) != 0 {
		if !responseStarted {
			writeGatewayError(writer, http.StatusBadGateway, "STREAM_POLICY_PROTOCOL_ERROR", "policy closed with unreleased bytes", requestID)
		}
		return
	}
	if !responseStarted {
		writer.WriteHeader(response.StatusCode)
	}
}

func (server *Server) forwardBuffered(writer http.ResponseWriter, response *http.Response, requestID string) {
	readLimit := server.maxResponseBytes
	if readLimit < math.MaxInt64 {
		readLimit++
	}
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, readLimit))
	if err != nil {
		writeGatewayError(writer, http.StatusBadGateway, "BACKEND_ERROR", "failed to read backend response", requestID)
		return
	}
	if int64(len(responseBody)) > server.maxResponseBytes {
		writeGatewayError(writer, http.StatusBadGateway, "BACKEND_RESPONSE_TOO_LARGE", "backend response exceeds configured limit", requestID)
		return
	}

	copyBackendHeaders(writer.Header(), response.Header)
	writer.WriteHeader(response.StatusCode)
	_, _ = writer.Write(responseBody)
}

func copyBackendHeaders(destination http.Header, source http.Header) {
	for _, name := range []string{"Content-Type", "Cache-Control"} {
		if value := source.Get(name); value != "" {
			destination.Set(name, value)
		}
	}
}

func newRequestID() (string, error) {
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", err
	}
	return "req_" + hex.EncodeToString(random[:]), nil
}

func writeGatewayError(writer http.ResponseWriter, status int, code string, message string, requestID string) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"error": map[string]string{
			"code":       code,
			"message":    message,
			"request_id": requestID,
		},
	})
}
