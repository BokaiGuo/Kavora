package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backend"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/backendstate"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/gateway"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/policyclient"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/telemetry"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/tenant"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/webui"
)

var version = "dev"

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	tenantConfigPath := environmentOrDefault("KAVORA_TENANT_CONFIG", "gateway/config.yaml")
	configData, err := os.ReadFile(tenantConfigPath)
	if err != nil {
		return errors.New("open tenant config " + tenantConfigPath + ": " + err.Error())
	}
	tenants, err := tenant.Load(bytes.NewReader(configData))
	if err != nil {
		return err
	}
	backends, err := backend.Load(configData)
	if err != nil {
		return err
	}

	policySocket := os.Getenv("KAVORA_POLICY_SOCKET")
	if policySocket == "" {
		var err error
		policySocket, err = policyclient.DefaultSocketPath()
		if err != nil {
			return err
		}
	}
	dialContext, cancelDial := context.WithTimeout(context.Background(), 5*time.Second)
	policy, err := policyclient.DialUDS(dialContext, policySocket)
	cancelDial()
	if err != nil {
		return err
	}
	defer policy.Close()

	backendURL := environmentOrDefault("KAVORA_BACKEND_URL", "http://127.0.0.1:18080")
	requestTimeout, err := environmentDuration("KAVORA_REQUEST_TIMEOUT", 60*time.Second)
	if err != nil {
		return err
	}
	streamChunkBytes, err := environmentPositiveInt("KAVORA_STREAM_CHUNK_BYTES", 16<<10)
	if err != nil {
		return err
	}
	streamBufferBytes, err := environmentPositiveInt("KAVORA_STREAM_BUFFER_BYTES", 64<<10)
	if err != nil {
		return err
	}
	metrics := telemetry.NewMetrics()
	audit := telemetry.NewAuditLogger(os.Stderr)
	kvRouter, err := loadRouter()
	if err != nil {
		return err
	}
	handler, err := gateway.New(gateway.Config{
		BackendURL:        backendURL,
		Policy:            policy,
		RequestTimeout:    requestTimeout,
		MaxRequestBytes:   1 << 20,
		MaxResponseBytes:  4 << 20,
		StreamChunkBytes:  streamChunkBytes,
		StreamBufferBytes: streamBufferBytes,
		StreamPolicy:      policy,
		Tenants:           tenants,
		Backends:          backends,
		Metrics:           metrics,
		Audit:             audit,
		Router:            kvRouter,
	})
	if err != nil {
		return err
	}

	listenAddress := environmentOrDefault("KAVORA_GATEWAY_LISTEN", "127.0.0.1:18000")
	server := &http.Server{
		Addr: listenAddress,
		Handler: webui.NewWithControlPlane(handler, metrics, func() bool {
			return backends == nil || backends.HealthyCount() > 0
		}, func() any {
			if backends == nil {
				return []backend.Status{}
			}
			return backends.Snapshot()
		}, kvRouter, os.Getenv("KAVORA_ADMIN_TOKEN")),
		ReadHeaderTimeout: 5 * time.Second,
	}
	shutdownContext, stopSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSignals()
	if stateURLs := backendStateURLs(os.Getenv("KAVORA_BACKEND_STATE_URLS")); len(stateURLs) > 0 {
		go runBackendStatePolling(shutdownContext, kvRouter, stateURLs)
	}
	if backends != nil {
		go runBackendHealthChecks(shutdownContext, backends)
	}
	go func() {
		<-shutdownContext.Done()
		gracefulContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(gracefulContext); err != nil {
			log.Printf("gateway shutdown: %v", err)
		}
	}()

	log.Printf("kavora-gateway %s listening on %s with backend %s", version, listenAddress, backendURL)
	err = server.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func backendStateURLs(raw string) []string {
	fields := strings.FieldsFunc(raw, func(character rune) bool {
		return character == ',' || character == ' ' || character == '\t' || character == '\n'
	})
	urls := make([]string, 0, len(fields))
	for _, field := range fields {
		if value := strings.TrimSpace(field); value != "" {
			urls = append(urls, value)
		}
	}
	return urls
}

func pollBackendStateOnce(ctx context.Context, client *http.Client, controller *router.Controller, urls []string) error {
	if client == nil {
		client = http.DefaultClient
	}
	var failures []error
	for _, stateURL := range urls {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, stateURL, nil)
		if err != nil {
			failures = append(failures, fmt.Errorf("backend state %s: %w", stateURL, err))
			continue
		}
		response, err := client.Do(request)
		if err != nil {
			failures = append(failures, fmt.Errorf("backend state %s: %w", stateURL, err))
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(response.Body, 1<<20))
		_ = response.Body.Close()
		if readErr != nil {
			failures = append(failures, fmt.Errorf("backend state %s: %w", stateURL, readErr))
			continue
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			failures = append(failures, fmt.Errorf("backend state %s: HTTP %d", stateURL, response.StatusCode))
			continue
		}
		var snapshots []backendstate.Snapshot
		if err := json.Unmarshal(body, &snapshots); err != nil {
			var snapshot backendstate.Snapshot
			if singleErr := json.Unmarshal(body, &snapshot); singleErr != nil {
				failures = append(failures, fmt.Errorf("backend state %s: decode: %w", stateURL, err))
				continue
			}
			snapshots = []backendstate.Snapshot{snapshot}
		}
		for _, snapshot := range snapshots {
			if err := controller.SetState(snapshot); err != nil {
				failures = append(failures, fmt.Errorf("backend state %s: %w", stateURL, err))
			}
		}
	}
	return errors.Join(failures...)
}

func runBackendStatePolling(ctx context.Context, controller *router.Controller, urls []string) {
	interval := 2 * time.Second
	if value := os.Getenv("KAVORA_BACKEND_STATE_POLL_INTERVAL"); value != "" {
		if parsed, err := time.ParseDuration(value); err == nil && parsed > 0 {
			interval = parsed
		}
	}
	client := &http.Client{Timeout: 3 * time.Second}
	poll := func() {
		if err := pollBackendStateOnce(ctx, client, controller, urls); err != nil && ctx.Err() == nil {
			log.Printf("backend state poll: %v", err)
		}
	}
	poll()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			poll()
		case <-ctx.Done():
			return
		}
	}
}

func loadRouter() (*router.Controller, error) {
	mode := router.Mode(os.Getenv("KAVORA_ROUTING_MODE"))
	affinity := router.NewAffinity(4096, 5*time.Minute)
	controller := router.NewController(mode, affinity)
	if err := configureOutcomeGrounding(controller); err != nil {
		return nil, err
	}
	maxStateAge, err := environmentDuration("KAVORA_BACKEND_STATE_MAX_AGE", 10*time.Second)
	if err != nil {
		return nil, err
	}
	controller.SetMaxStateAge(maxStateAge)
	switch environmentOrDefault("KAVORA_CACHE_FIDELITY", "affinity") {
	case "none":
		controller.SetCacheProvider(router.NoCacheProvider{})
	case "affinity":
		controller.SetCacheProvider(router.NewAffinityProvider(affinity, .65, nil))
	case "shadow":
		controller.SetCacheProvider(router.NewShadowIndexProvider(.55, maxStateAge, nil))
	case "exact", "kv-events":
		lambda, err := environmentNonNegativeFloat("KAVORA_CACHE_CONFIDENCE_LAMBDA", .1)
		if err != nil {
			return nil, err
		}
		controller.SetCacheProvider(router.NewKVEventProvider(65536, maxStateAge, lambda, nil))
	default:
		return nil, errors.New("KAVORA_CACHE_FIDELITY must be none, affinity, shadow, or exact")
	}
	if lifecyclePath := os.Getenv("KAVORA_ROUTING_LIFECYCLE_CONFIG"); lifecyclePath != "" {
		data, err := os.ReadFile(lifecyclePath)
		if err != nil {
			return nil, fmt.Errorf("read routing lifecycle: %w", err)
		}
		lifecycle, err := router.LoadLifecycle(data)
		if err != nil {
			return nil, err
		}
		controller.SetLifecycle(lifecycle)
	}
	path := os.Getenv("KAVORA_BACKEND_STATE_FILE")
	if path == "" {
		return controller, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read backend state: %w", err)
	}
	var snapshots []backendstate.Snapshot
	if err := json.Unmarshal(data, &snapshots); err != nil {
		var snapshot backendstate.Snapshot
		if singleErr := json.Unmarshal(data, &snapshot); singleErr != nil {
			return nil, fmt.Errorf("decode backend state: %w", err)
		}
		snapshots = []backendstate.Snapshot{snapshot}
	}
	for _, snapshot := range snapshots {
		if err := controller.SetState(snapshot); err != nil {
			return nil, err
		}
	}
	return controller, nil
}

func configureOutcomeGrounding(controller *router.Controller) error {
	journalDirectory := environmentOrDefault("KAVORA_DECISION_JOURNAL_DIR", "results/state")
	if journalDirectory != "off" && journalDirectory != "disabled" {
		journal, err := router.OpenDecisionJournal(journalDirectory, nil)
		if err != nil {
			return fmt.Errorf("open decision journal: %w", err)
		}
		ledger, err := router.NewDecisionLedgerWithJournal(4096, journal)
		if err != nil {
			return fmt.Errorf("restore decision journal: %w", err)
		}
		controller.SetLedger(ledger)
	}
	if predictorPath := os.Getenv("KAVORA_TTFT_PREDICTOR_PATH"); predictorPath != "" {
		data, err := os.ReadFile(predictorPath)
		if err != nil {
			return fmt.Errorf("read TTFT predictor: %w", err)
		}
		predictor, _, err := router.LoadTTFTPredictor(data)
		if err != nil {
			return fmt.Errorf("load TTFT predictor: %w", err)
		}
		controller.SetPredictor(predictor)
	}
	return nil
}

func runBackendHealthChecks(ctx context.Context, registry *backend.Registry) {
	interval := 15 * time.Second
	if value := os.Getenv("KAVORA_BACKEND_HEALTH_INTERVAL"); value != "" {
		if parsed, err := time.ParseDuration(value); err == nil && parsed > 0 {
			interval = parsed
		}
	}
	client := &http.Client{Timeout: 3 * time.Second}
	check := func() {
		if err := registry.CheckHealth(ctx, client); err != nil {
			log.Printf("backend health check: %v", err)
		}
	}
	check()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			check()
		case <-ctx.Done():
			return
		}
	}
}

func environmentOrDefault(name string, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func environmentDuration(name string, fallback time.Duration) (time.Duration, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		return 0, errors.New(name + " must be a positive duration")
	}
	return parsed, nil
}

func environmentPositiveInt(name string, fallback int) (int, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, errors.New(name + " must be a positive integer")
	}
	return parsed, nil
}

func environmentNonNegativeFloat(name string, fallback float64) (float64, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed < 0 {
		return 0, errors.New(name + " must be a non-negative number")
	}
	return parsed, nil
}
