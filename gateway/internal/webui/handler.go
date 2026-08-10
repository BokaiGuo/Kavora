package webui

import (
	"embed"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/router"
	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/telemetry"
)

//go:embed static
var staticFiles embed.FS

func New(gateway http.Handler) http.Handler {
	return NewWithObservability(gateway, nil, nil, nil)
}

func NewWithObservability(gateway http.Handler, metrics *telemetry.Metrics, ready func() bool, backends func() any) http.Handler {
	return NewWithControlPlane(gateway, metrics, ready, backends, nil, "")
}

func NewWithControlPlane(gateway http.Handler, metrics *telemetry.Metrics, ready func() bool, backends func() any, controller *router.Controller, adminToken string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]string{"status": "ok", "service": "kavora-gateway"})
	})
	mux.HandleFunc("/readyz", func(writer http.ResponseWriter, _ *http.Request) {
		readyNow := ready == nil || ready()
		writer.Header().Set("Content-Type", "application/json")
		if !readyNow {
			writer.WriteHeader(http.StatusServiceUnavailable)
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{"ready": readyNow, "service": "kavora-gateway"})
	})
	mux.HandleFunc("/metrics", func(writer http.ResponseWriter, _ *http.Request) {
		if metrics == nil {
			http.Error(writer, "metrics unavailable", http.StatusNotImplemented)
			return
		}
		writer.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_, _ = writer.Write([]byte(metrics.Render()))
	})
	mux.HandleFunc("/api/backends", func(writer http.ResponseWriter, _ *http.Request) {
		if backends == nil {
			http.Error(writer, "backend status unavailable", http.StatusNotImplemented)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{"backends": backends()})
	})
	if controller != nil {
		mux.HandleFunc("/v1/admin/decisions", admin(adminToken, func(writer http.ResponseWriter, request *http.Request) {
			if request.Method != http.MethodGet {
				http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			limit, _ := strconv.Atoi(request.URL.Query().Get("limit"))
			writeJSON(writer, http.StatusOK, map[string]any{"decisions": controller.Ledger().Recent(limit)})
		}))
		mux.HandleFunc("/v1/admin/decisions/", admin(adminToken, func(writer http.ResponseWriter, request *http.Request) {
			if request.Method != http.MethodGet {
				http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			requestID := strings.TrimPrefix(request.URL.Path, "/v1/admin/decisions/")
			decision, ok := controller.Ledger().Get(requestID)
			if !ok {
				http.Error(writer, "decision not found", http.StatusNotFound)
				return
			}
			writeJSON(writer, http.StatusOK, decision)
		}))
		mux.HandleFunc("/v1/admin/lifecycle", admin(adminToken, func(writer http.ResponseWriter, request *http.Request) {
			lifecycle := controller.Lifecycle()
			if lifecycle == nil {
				http.Error(writer, "lifecycle unavailable", http.StatusNotImplemented)
				return
			}
			switch request.Method {
			case http.MethodGet:
				writeJSON(writer, http.StatusOK, lifecycle.Snapshot())
			case http.MethodPost:
				var observation router.LifecycleObservation
				if err := decodeJSON(request, &observation); err != nil {
					http.Error(writer, err.Error(), http.StatusBadRequest)
					return
				}
				writeJSON(writer, http.StatusOK, lifecycle.Observe(observation))
			default:
				http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
			}
		}))
		mux.HandleFunc("/v1/admin/lifecycle/approve", admin(adminToken, func(writer http.ResponseWriter, request *http.Request) {
			if request.Method != http.MethodPost {
				http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			lifecycle := controller.Lifecycle()
			if lifecycle == nil {
				http.Error(writer, "lifecycle unavailable", http.StatusNotImplemented)
				return
			}
			var approval router.LifecycleApproval
			if err := decodeJSON(request, &approval); err != nil || strings.TrimSpace(approval.ApprovedBy) == "" {
				http.Error(writer, "approved_by is required", http.StatusBadRequest)
				return
			}
			writeJSON(writer, http.StatusOK, lifecycle.Approve(approval))
		}))
		mux.HandleFunc("/v1/admin/cache-events", admin(adminToken, func(writer http.ResponseWriter, request *http.Request) {
			if request.Method != http.MethodPost {
				http.Error(writer, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			var event router.KVEvent
			if err := decodeJSON(request, &event); err != nil {
				http.Error(writer, err.Error(), http.StatusBadRequest)
				return
			}
			if event.BackendID == "" || event.CacheKey == "" || event.MatchedTokens < 0 || event.TotalTokens < 0 || (event.TotalTokens > 0 && event.MatchedTokens > event.TotalTokens) {
				http.Error(writer, "invalid cache event", http.StatusBadRequest)
				return
			}
			if !controller.ObserveKVEvent(event) {
				http.Error(writer, "kv event provider is not active", http.StatusConflict)
				return
			}
			writeJSON(writer, http.StatusAccepted, map[string]bool{"accepted": true})
		}))
	}
	mux.HandleFunc("/ui", func(writer http.ResponseWriter, request *http.Request) {
		http.Redirect(writer, request, "/ui/", http.StatusMovedPermanently)
	})
	mux.HandleFunc("/ui/", func(writer http.ResponseWriter, request *http.Request) {
		path := strings.TrimPrefix(request.URL.Path, "/ui/")
		if path == "" {
			path = "index.html"
		}
		data, err := staticFiles.ReadFile("static/" + path)
		if err != nil {
			data, err = staticFiles.ReadFile("static/index.html")
			if err != nil {
				http.Error(writer, "UI unavailable", http.StatusInternalServerError)
				return
			}
		}
		if strings.HasSuffix(path, ".css") {
			writer.Header().Set("Content-Type", "text/css; charset=utf-8")
		} else if strings.HasSuffix(path, ".js") {
			writer.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		} else {
			writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		}
		_, _ = writer.Write(data)
	})
	mux.Handle("/", gateway)
	return mux
}

func admin(token string, handler http.HandlerFunc) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		if token != "" && request.Header.Get("Authorization") != "Bearer "+token {
			writer.Header().Set("WWW-Authenticate", "Bearer")
			http.Error(writer, "unauthorized", http.StatusUnauthorized)
			return
		}
		handler(writer, request)
	}
}

func decodeJSON(request *http.Request, target any) error {
	defer request.Body.Close()
	return json.NewDecoder(io.LimitReader(request.Body, 1<<20)).Decode(target)
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}
