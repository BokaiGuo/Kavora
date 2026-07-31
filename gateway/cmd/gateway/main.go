package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
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
		Handler: webui.NewWithObservability(handler, metrics, func() bool {
			return backends == nil || backends.HealthyCount() > 0
		}, func() any {
			if backends == nil {
				return []backend.Status{}
			}
			return backends.Snapshot()
		}),
		ReadHeaderTimeout: 5 * time.Second,
	}
	shutdownContext, stopSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSignals()
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

func loadRouter() (*router.Controller, error) {
	mode := router.Mode(os.Getenv("KAVORA_ROUTING_MODE"))
	controller := router.NewController(mode, router.NewAffinity(4096, 5*time.Minute))
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
