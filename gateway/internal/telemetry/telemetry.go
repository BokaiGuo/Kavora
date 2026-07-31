package telemetry

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

var durationBuckets = []int64{1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000, 250_000_000, 500_000_000, 1_000_000_000, 5_000_000_000}

type Metrics struct {
	requests  sync.Map
	policies  sync.Map
	backends  sync.Map
	durations sync.Map
	inflight  atomic.Int64
}

type counter struct{ value atomic.Uint64 }

func NewMetrics() *Metrics { return &Metrics{} }

func (metrics *Metrics) IncRequest(endpoint, outcome string) {
	metrics.counter(&metrics.requests, endpoint+"\x00"+outcome).value.Add(1)
}

func (metrics *Metrics) IncPolicy(decision string) {
	metrics.counter(&metrics.policies, decision).value.Add(1)
}

func (metrics *Metrics) IncBackend(backendID, outcome string) {
	metrics.counter(&metrics.backends, backendID+"\x00"+outcome).value.Add(1)
}

func (metrics *Metrics) ObserveRequest(endpoint, outcome string, duration time.Duration) {
	key := endpoint + "\x00" + outcome
	value, _ := metrics.durations.LoadOrStore(key, &durationHistogram{buckets: make([]atomic.Uint64, len(durationBuckets)+1)})
	histogram := value.(*durationHistogram)
	for index, bucket := range durationBuckets {
		if duration.Nanoseconds() <= bucket {
			histogram.buckets[index].Add(1)
		}
	}
	histogram.buckets[len(durationBuckets)].Add(1)
}

func (metrics *Metrics) InflightAdd(delta int64) { metrics.inflight.Add(delta) }

type durationHistogram struct{ buckets []atomic.Uint64 }

func (metrics *Metrics) counter(store *sync.Map, key string) *counter {
	value, _ := store.LoadOrStore(key, &counter{})
	return value.(*counter)
}

func (metrics *Metrics) Render() string {
	output := "# TYPE kavora_requests_total counter\n"
	output += renderCounters(&output, "kavora_requests_total", &metrics.requests, func(key string) string {
		parts := splitKey(key)
		return `endpoint="` + escape(parts[0]) + `",outcome="` + escape(parts[1]) + `"`
	})
	output += "# TYPE kavora_policy_decisions_total counter\n"
	output += renderCounters(&output, "kavora_policy_decisions_total", &metrics.policies, func(key string) string { return `decision="` + escape(key) + `"` })
	output += "# TYPE kavora_backend_attempts_total counter\n"
	output += renderCounters(&output, "kavora_backend_attempts_total", &metrics.backends, func(key string) string {
		parts := splitKey(key)
		return `backend="` + escape(parts[0]) + `",outcome="` + escape(parts[1]) + `"`
	})
	output += "# TYPE kavora_request_duration_seconds histogram\n"
	keys := mapKeys(&metrics.durations)
	for _, key := range keys {
		histogram, _ := metrics.durations.Load(key)
		parts := splitKey(key)
		labels := `endpoint="` + escape(parts[0]) + `",outcome="` + escape(parts[1]) + `"`
		value := histogram.(*durationHistogram)
		for index, bucket := range durationBuckets {
			output += `kavora_request_duration_seconds_bucket{` + labels + `,le="` + strconv.FormatFloat(float64(bucket)/1e9, 'f', -9, 64) + `"} ` + strconv.FormatUint(value.buckets[index].Load(), 10) + "\n"
		}
		output += `kavora_request_duration_seconds_bucket{` + labels + `,le="+Inf"} ` + strconv.FormatUint(value.buckets[len(durationBuckets)].Load(), 10) + "\n"
	}
	output += "# TYPE kavora_inflight_requests gauge\n"
	output += "kavora_inflight_requests " + strconv.FormatInt(metrics.inflight.Load(), 10) + "\n"
	return output
}

func renderCounters(output *string, name string, store *sync.Map, labels func(string) string) string {
	result := ""
	keys := mapKeys(store)
	for _, key := range keys {
		value, _ := store.Load(key)
		result += name + "{" + labels(key) + "} " + strconv.FormatUint(value.(*counter).value.Load(), 10) + "\n"
	}
	return result
}

func mapKeys(store *sync.Map) []string {
	keys := []string{}
	store.Range(func(key, _ any) bool { keys = append(keys, key.(string)); return true })
	sort.Strings(keys)
	return keys
}

func splitKey(key string) []string {
	for index, value := range key {
		if value == '\x00' {
			return []string{key[:index], key[index+1:]}
		}
	}
	return []string{key, ""}
}

func escape(value string) string {
	return strconv.Quote(value)[1 : len(strconv.Quote(value))-1]
}

type AuditEvent struct {
	Event      string    `json:"event"`
	RequestID  string    `json:"request_id,omitempty"`
	TenantID   string    `json:"tenant_id,omitempty"`
	Policy     string    `json:"policy_decision,omitempty"`
	BackendID  string    `json:"backend_id,omitempty"`
	Outcome    string    `json:"outcome,omitempty"`
	ErrorCode  string    `json:"error_code,omitempty"`
	Stream     bool      `json:"stream,omitempty"`
	DurationMS int64     `json:"duration_ms,omitempty"`
	APIKey     string    `json:"-"`
	Prompt     string    `json:"-"`
	OccurredAt time.Time `json:"occurred_at"`
}

type AuditLogger struct {
	mu     sync.Mutex
	writer io.Writer
}

func NewAuditLogger(writer io.Writer) *AuditLogger { return &AuditLogger{writer: writer} }

func (logger *AuditLogger) Log(event AuditEvent) {
	if logger == nil || logger.writer == nil {
		return
	}
	event.OccurredAt = time.Now().UTC()
	logger.mu.Lock()
	defer logger.mu.Unlock()
	data, err := json.Marshal(event)
	if err == nil {
		_, _ = fmt.Fprintln(logger.writer, string(data))
	}
}
