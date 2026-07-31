package telemetry

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestMetricsRenderPrometheusText(t *testing.T) {
	metrics := NewMetrics()
	metrics.IncRequest("/v1/chat/completions", "success")
	metrics.ObserveRequest("/v1/chat/completions", "success", 25_000_000)
	metrics.IncPolicy("allow")
	metrics.IncBackend("local", "success")
	output := metrics.Render()
	for _, want := range []string{
		`kavora_requests_total{endpoint="/v1/chat/completions",outcome="success"} 1`,
		`kavora_policy_decisions_total{decision="allow"} 1`,
		`kavora_backend_attempts_total{backend="local",outcome="success"} 1`,
		`kavora_request_duration_seconds_bucket`,
	} {
		if !strings.Contains(output, want) {
			t.Fatalf("metrics missing %q in %s", want, output)
		}
	}
}

func TestAuditLoggerRedactsPromptAndAPIKey(t *testing.T) {
	var output bytes.Buffer
	logger := NewAuditLogger(&output)
	logger.Log(AuditEvent{Event: "request_completed", RequestID: "req_1", TenantID: "team-a", APIKey: "secret", Prompt: "private prompt", Outcome: "success"})
	var record map[string]any
	if err := json.Unmarshal(output.Bytes(), &record); err != nil {
		t.Fatal(err)
	}
	if record["request_id"] != "req_1" || record["tenant_id"] != "team-a" || record["outcome"] != "success" {
		t.Fatalf("record = %+v", record)
	}
	if strings.Contains(output.String(), "secret") || strings.Contains(output.String(), "private prompt") {
		t.Fatalf("audit leaked sensitive data: %s", output.String())
	}
}
