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
	"sort"
	"strconv"
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
	CacheKeyResolver  CacheKeyResolver
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
	cacheKeyResolver  CacheKeyResolver
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
		cacheKeyResolver:  config.CacheKeyResolver,
		tenants:           config.Tenants,
		limiter:           limiter,
	}, nil
}

func (server *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	started := time.Now()
	observed := &observedWriter{ResponseWriter: writer}
	requestID := ""
	realized := requestOutcomeObservation{}
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
		if server.router != nil && requestID != "" && realized.routed {
			ttftMS := 0.0
			if realized.ttftMS != nil {
				ttftMS = *realized.ttftMS
			} else if isStream && !observed.firstWriteAt.IsZero() {
				ttftMS = float64(observed.firstWriteAt.Sub(started).Microseconds()) / 1000
			}
			server.router.RecordOutcome(router.DecisionOutcome{
				RequestID:             requestID,
				ActualBackend:         realized.actualBackend,
				TTFTMS:                ttftMS,
				E2EMS:                 float64(time.Since(started).Microseconds()) / 1000,
				Success:               status >= 200 && status < 300,
				StatusCode:            status,
				PromptTokens:          realized.promptTokens,
				OutputTokens:          realized.outputTokens,
				Model:                 realized.model,
				GPUType:               realized.gpuType,
				BackendEngine:         realized.backendEngine,
				BackendVersion:        realized.backendVersion,
				ObservedCacheHitRatio: realized.cacheHitRatio,
				ObservedMatchedTokens: realized.matchedTokens,
				TPOTMS:                realized.tpotMS(observed),
				StreamGapP95MS:        realized.streamGapP95MS(observed),
				StreamChunkCount:      len(observed.writeTimes),
				CompletedAt:           time.Now().UTC(),
			})
		}
	}()
	if request.Method != http.MethodPost || request.URL.Path != "/v1/chat/completions" {
		http.NotFound(writer, request)
		return
	}

	var err error
	requestID, err = newRequestID()
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

	cacheKey := policyRequest.Request.Model
	if result != nil && len(result.CacheKey) > 0 {
		cacheKey = hex.EncodeToString(result.CacheKey)
	}
	promptTokens := 0
	if result != nil && result.EstimatedTokens <= uint64(math.MaxInt) {
		promptTokens = int(result.EstimatedTokens)
	}
	externalCacheKeys := []string(nil)
	if server.cacheKeyResolver != nil {
		resolved, resolveErr := server.cacheKeyResolver.Resolve(ctx, rawBody)
		if resolveErr == nil {
			externalCacheKeys = resolved.CacheKeys
			if resolved.TokenCount > 0 {
				promptTokens = resolved.TokenCount
			}
			writer.Header().Set("X-Kavora-Hash-Alignment", "vllm-exact")
		} else {
			writer.Header().Set("X-Kavora-Hash-Alignment", "unavailable")
		}
	}
	realized.promptTokens = promptTokens
	realized.model = policyRequest.Request.Model
	server.forward(writer, ctx, rawBody, policyRequest, requestID, activeTenant, cacheKey, externalCacheKeys, promptTokens, stream, &realized)
}

func metricsOrDefault(metrics *telemetry.Metrics) *telemetry.Metrics {
	if metrics == nil {
		return telemetry.NewMetrics()
	}
	return metrics
}

type observedWriter struct {
	http.ResponseWriter
	status       int
	firstWriteAt time.Time
	writeTimes   []time.Time
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
	if len(data) > 0 && writer.firstWriteAt.IsZero() {
		writer.firstWriteAt = time.Now()
	}
	if len(data) > 0 {
		writer.writeTimes = append(writer.writeTimes, time.Now())
	}
	return writer.ResponseWriter.Write(data)
}

type requestOutcomeObservation struct {
	routed         bool
	actualBackend  string
	promptTokens   int
	outputTokens   int
	model          string
	gpuType        string
	backendEngine  string
	backendVersion string
	ttftMS         *float64
	cacheHitRatio  *float64
	matchedTokens  *int
}

func (observation requestOutcomeObservation) tpotMS(writer *observedWriter) *float64 {
	if len(writer.writeTimes) < 2 || observation.outputTokens <= 1 {
		return nil
	}
	value := float64(writer.writeTimes[len(writer.writeTimes)-1].Sub(writer.writeTimes[0]).Microseconds()) / 1000 / float64(observation.outputTokens-1)
	if value < 0 {
		return nil
	}
	return &value
}

func (observation requestOutcomeObservation) streamGapP95MS(writer *observedWriter) *float64 {
	if len(writer.writeTimes) < 2 {
		return nil
	}
	intervals := make([]float64, 0, len(writer.writeTimes)-1)
	for index := 1; index < len(writer.writeTimes); index++ {
		intervals = append(intervals, float64(writer.writeTimes[index].Sub(writer.writeTimes[index-1]).Microseconds())/1000)
	}
	sort.Float64s(intervals)
	position := .95 * float64(len(intervals)-1)
	lower := int(position)
	upper := lower
	if lower < len(intervals)-1 {
		upper++
	}
	value := intervals[lower]
	if upper != lower {
		value += (intervals[upper] - intervals[lower]) * (position - float64(lower))
	}
	return &value
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
	activeTenant tenant.Tenant,
	cacheKey string,
	externalCacheKeys []string,
	promptTokens int,
	stream bool,
	realized *requestOutcomeObservation,
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

	candidates, routingDecision := server.backendCandidates(requestID, activeTenant, cacheKey, externalCacheKeys, promptTokens, policyRequest.Request.Model)
	realized.routed = true
	writer.Header().Set("X-Kavora-Routing-Mode", routingDecision.Mode)
	writer.Header().Set("X-Kavora-Routing-Fallback", strconv.FormatBool(routingDecision.Fallback))
	if routingDecision.Selected != "" {
		writer.Header().Set("X-Kavora-Routing-Suggested", routingDecision.Selected)
	}
	if len(candidates) == 0 {
		writeGatewayError(writer, http.StatusServiceUnavailable, "BACKEND_UNAVAILABLE", "no healthy backend supports the requested model", requestID)
		return
	}
	for _, candidate := range candidates {
		realized.actualBackend = candidate.ID
		realized.gpuType = candidate.Attributes["gpu_type"]
		realized.backendEngine = candidate.Attributes["engine"]
		realized.backendVersion = candidate.Attributes["engine_version"]
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
		writer.Header().Set("X-Kavora-Backend", candidate.ID)
		realized.ttftMS = optionalHeaderPositiveFloat(response.Header, "X-Kavora-TTFT-MS")
		realized.cacheHitRatio = optionalHeaderFloat(response.Header, "X-Kavora-Cache-Hit-Ratio")
		realized.matchedTokens = optionalHeaderInt(response.Header, "X-Kavora-Matched-Tokens")
		if realized.cacheHitRatio == nil && realized.matchedTokens != nil && realized.promptTokens > 0 {
			ratio := math.Min(1, float64(*realized.matchedTokens)/float64(realized.promptTokens))
			realized.cacheHitRatio = &ratio
		}
		if server.router != nil {
			server.router.RecordActual(requestID, candidate.ID)
		}
		if stream {
			realized.outputTokens = optionalHeaderIntValue(response.Header, "X-Kavora-Output-Tokens")
			server.forwardStream(writer, response, flusher, streamSession, requestID, policyRequest.Context.FailMode)
		} else {
			realized.outputTokens = server.forwardBuffered(writer, response, requestID)
		}
		_ = response.Body.Close()
		return
	}
	writeGatewayError(writer, http.StatusBadGateway, "BACKEND_UNAVAILABLE", "all candidate backends failed before response", requestID)
}

func (server *Server) backendCandidates(requestID string, activeTenant tenant.Tenant, cacheKey string, externalCacheKeys []string, promptTokens int, model string) ([]backend.Backend, router.Decision) {
	var candidates []backend.Backend
	if server.backends != nil {
		candidates = server.backends.Candidates(model)
	} else {
		candidates = []backend.Backend{{ID: "default", URL: server.backendURL}}
	}
	if server.router == nil {
		return candidates, router.Decision{RequestID: requestID, TenantID: activeTenant.ID, Mode: string(router.ModeStatic), Reason: "static_round_robin"}
	}
	descriptors := make([]router.BackendDescriptor, 0, len(candidates))
	for _, candidate := range candidates {
		descriptors = append(descriptors, router.BackendDescriptor{ID: candidate.ID, Attributes: candidate.Attributes})
	}
	decision := server.router.Plan(context.Background(), router.RoutingRequest{
		RequestID: requestID, TenantID: activeTenant.ID, Model: model, CacheKey: cacheKey, ExternalCacheKeys: externalCacheKeys, PromptTokens: promptTokens,
		Requirements: activeTenant.RoutingRequirements, TTFTSLOMS: activeTenant.TTFTSLOMS,
	}, descriptors)
	eligible := map[string]bool{}
	for _, candidate := range decision.Candidates {
		if candidate.Eligible {
			eligible[candidate.BackendID] = true
		}
	}
	filtered := candidates[:0]
	for _, candidate := range candidates {
		if eligible[candidate.ID] {
			filtered = append(filtered, candidate)
		}
	}
	candidates = filtered
	preferred := server.router.PreferredIDsForDecision(decision, cacheKey)
	if len(preferred) == 0 {
		return candidates, decision
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
	return ordered, decision
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

func (server *Server) forwardBuffered(writer http.ResponseWriter, response *http.Response, requestID string) int {
	readLimit := server.maxResponseBytes
	if readLimit < math.MaxInt64 {
		readLimit++
	}
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, readLimit))
	if err != nil {
		writeGatewayError(writer, http.StatusBadGateway, "BACKEND_ERROR", "failed to read backend response", requestID)
		return 0
	}
	if int64(len(responseBody)) > server.maxResponseBytes {
		writeGatewayError(writer, http.StatusBadGateway, "BACKEND_RESPONSE_TOO_LARGE", "backend response exceeds configured limit", requestID)
		return 0
	}

	copyBackendHeaders(writer.Header(), response.Header)
	writer.WriteHeader(response.StatusCode)
	_, _ = writer.Write(responseBody)
	return completionTokens(responseBody)
}

func completionTokens(data []byte) int {
	var payload struct {
		Usage struct {
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if json.Unmarshal(data, &payload) != nil || payload.Usage.CompletionTokens < 0 {
		return 0
	}
	return payload.Usage.CompletionTokens
}

func optionalHeaderFloat(header http.Header, name string) *float64 {
	value, err := strconv.ParseFloat(header.Get(name), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 || value > 1 {
		return nil
	}
	return &value
}

func optionalHeaderPositiveFloat(header http.Header, name string) *float64 {
	value, err := strconv.ParseFloat(header.Get(name), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value <= 0 {
		return nil
	}
	return &value
}

func optionalHeaderInt(header http.Header, name string) *int {
	value, err := strconv.Atoi(header.Get(name))
	if err != nil || value < 0 {
		return nil
	}
	return &value
}

func optionalHeaderIntValue(header http.Header, name string) int {
	value := optionalHeaderInt(header, name)
	if value == nil {
		return 0
	}
	return *value
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
