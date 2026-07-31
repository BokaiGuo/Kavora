package webui

import (
	"embed"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/telemetry"
)

//go:embed static
var staticFiles embed.FS

func New(gateway http.Handler) http.Handler {
	return NewWithObservability(gateway, nil, nil, nil)
}

func NewWithObservability(gateway http.Handler, metrics *telemetry.Metrics, ready func() bool, backends func() any) http.Handler {
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
