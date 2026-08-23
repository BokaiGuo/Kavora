package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/client"
)

type uiSnapshot struct {
	Health   map[string]any
	Backends []client.BackendStatus
	Advice   map[string]any
	Errors   []string
	At       time.Time
}

type uiTheme struct {
	Color  bool
	Clear  bool
	Reset  string
	Dim    string
	Cyan   string
	Green  string
	Yellow string
	Red    string
	Blue   string
	White  string
}

func runUI(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	flags := flag.NewFlagSet("ui", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	apiKey := flags.String("api-key", "", "one-off API key; prefer KAVORA_API_KEY")
	interval := flags.Duration("interval", 2*time.Second, "refresh interval")
	once := flags.Bool("once", false, "render one snapshot and exit")
	noColor := flags.Bool("no-color", false, "disable ANSI color and animation")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *interval < 200*time.Millisecond {
		return fmt.Errorf("--interval must be at least 200ms")
	}
	key, _, err := loadCredentials(*apiKey)
	if err != nil && !jsonOutput {
		key = ""
	}
	if err != nil {
		key = ""
	}
	gateway := client.New(*baseURL, key)
	if jsonOutput {
		snapshot := collectUISnapshot(gateway)
		return writeJSONSnapshot(stdout, snapshot)
	}

	theme := newUITheme(!*noColor && terminalWriter(stdout))
	interactive := theme.Clear && !*once && terminalWriter(os.Stdin)
	if interactive {
		fmt.Fprint(stdout, "\x1b[?25l")
		defer fmt.Fprint(stdout, "\x1b[?25h\x1b[0m\n")
	}
	spinner := 0
	input := make(chan byte, 8)
	if interactive {
		go readUIInput(os.Stdin, input)
	}
	for {
		snapshot := collectUISnapshot(gateway)
		renderUIDashboard(stdout, snapshot, theme, spinner, *baseURL)
		if *once || !interactive {
			return nil
		}
		spinner++
		select {
		case key := <-input:
			if key == 'q' || key == 'Q' {
				return nil
			}
			if key == 'r' || key == 'R' {
				continue
			}
		case <-time.After(*interval):
		}
	}
}

func readUIInput(reader io.Reader, input chan<- byte) {
	buffer := make([]byte, 1)
	for {
		if _, err := reader.Read(buffer); err != nil {
			return
		}
		input <- buffer[0]
	}
}

func terminalWriter(writer io.Writer) bool {
	file, ok := writer.(*os.File)
	if !ok {
		return false
	}
	info, err := file.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
}

func newUITheme(color bool) uiTheme {
	theme := uiTheme{}
	if !color {
		return theme
	}
	theme.Color = true
	theme.Clear = true
	theme.Reset = "\x1b[0m"
	theme.Dim = "\x1b[2m"
	theme.Cyan = "\x1b[36m"
	theme.Green = "\x1b[32m"
	theme.Yellow = "\x1b[33m"
	theme.Red = "\x1b[31m"
	theme.Blue = "\x1b[34m"
	theme.White = "\x1b[97m"
	return theme
}

func collectUISnapshot(gateway *client.Client) uiSnapshot {
	snapshot := uiSnapshot{At: time.Now()}
	var group sync.WaitGroup
	var mu sync.Mutex
	group.Add(3)
	go func() {
		defer group.Done()
		health, err := gateway.Doctor(context.Background())
		mu.Lock()
		defer mu.Unlock()
		if err != nil {
			snapshot.Errors = append(snapshot.Errors, "health: "+err.Error())
		} else {
			snapshot.Health = health
		}
	}()
	go func() {
		defer group.Done()
		backends, err := gateway.Backends(context.Background())
		mu.Lock()
		defer mu.Unlock()
		if err != nil {
			snapshot.Errors = append(snapshot.Errors, "backends: "+err.Error())
		} else {
			snapshot.Backends = backends
		}
	}()
	go func() {
		defer group.Done()
		advice, err := gateway.Advice(context.Background())
		mu.Lock()
		defer mu.Unlock()
		if err != nil {
			snapshot.Errors = append(snapshot.Errors, "advice: "+err.Error())
		} else {
			snapshot.Advice = advice
		}
	}()
	group.Wait()
	sort.Strings(snapshot.Errors)
	return snapshot
}

func writeJSONSnapshot(writer io.Writer, snapshot uiSnapshot) error {
	return json.NewEncoder(writer).Encode(map[string]any{
		"at":       snapshot.At.UTC().Format(time.RFC3339),
		"health":   snapshot.Health,
		"backends": snapshot.Backends,
		"advice":   snapshot.Advice,
		"errors":   snapshot.Errors,
	})
}

func renderUIDashboard(writer io.Writer, snapshot uiSnapshot, theme uiTheme, frame int, baseURL string) {
	if theme.Clear {
		fmt.Fprint(writer, "\x1b[2J\x1b[H")
	}
	spinner := []string{"◒", "◐", "◓", "◑"}[frame%4]
	healthOK := len(snapshot.Health) > 0
	healthy := 0
	for _, backend := range snapshot.Backends {
		if backend.Healthy {
			healthy++
		}
	}
	statusText := "OFFLINE"
	statusColor := theme.Red
	if healthOK {
		statusText = "ONLINE"
		statusColor = theme.Green
	}
	fmt.Fprintf(writer, "%s%s  KAVORA CONTROL SURFACE%s  %s%s%s\n", theme.Cyan, spinner, theme.Reset, statusColor, statusText, theme.Reset)
	fmt.Fprintf(writer, "%s  gateway%s  %s%s%s  %srefresh %s%s\n", theme.Dim, theme.Reset, theme.White, baseURL, theme.Reset, theme.Dim, snapshot.At.Format("15:04:05"), theme.Reset)
	fmt.Fprintln(writer, "")
	fmt.Fprintf(writer, "%s  BACKENDS%s  %s%d/%d healthy%s\n", theme.Blue, theme.Reset, statusColor, healthy, len(snapshot.Backends), theme.Reset)
	if len(snapshot.Backends) == 0 {
		fmt.Fprintf(writer, "  %sno backend telemetry available%s\n", theme.Dim, theme.Reset)
	}
	for _, backend := range snapshot.Backends {
		state := "DOWN"
		color := theme.Red
		if backend.Healthy {
			state = "READY"
			color = theme.Green
		}
		bar := uiBar(backend.Weight, 10)
		fmt.Fprintf(writer, "  %s%-18s%s %s%-5s%s  weight %s  %s\n", theme.White, backend.ID, theme.Reset, color, state, theme.Reset, bar, backend.URL)
	}
	fmt.Fprintln(writer, "")
	fmt.Fprintf(writer, "%s  ADVISOR%s\n", theme.Yellow, theme.Reset)
	recommendations := uiRecommendations(snapshot.Advice)
	if len(recommendations) == 0 {
		fmt.Fprintf(writer, "  %squiet — no active recommendations%s\n", theme.Dim, theme.Reset)
	} else {
		for _, recommendation := range recommendations {
			fmt.Fprintf(writer, "  %s%s%s\n", theme.Yellow, recommendation, theme.Reset)
		}
	}
	if len(snapshot.Errors) > 0 {
		fmt.Fprintln(writer, "")
		fmt.Fprintf(writer, "%s  SIGNALS%s\n", theme.Red, theme.Reset)
		for _, item := range snapshot.Errors {
			fmt.Fprintf(writer, "  %s!%s %s\n", theme.Red, theme.Reset, item)
		}
	}
	fmt.Fprintln(writer, "")
	if theme.Clear {
		fmt.Fprintf(writer, "%s  q / Ctrl-C quit   r refresh   live evidence-aware routing%s\n", theme.Dim, theme.Reset)
	} else {
		fmt.Fprintf(writer, "%s  snapshot mode — run `kavora ui` in a TTY for live animation%s\n", theme.Dim, theme.Reset)
	}
}

func uiBar(value, width int) string {
	if value < 0 {
		value = 0
	}
	if value > width {
		value = width
	}
	return "[" + strings.Repeat("█", value) + strings.Repeat("·", width-value) + "]"
}

func uiRecommendations(advice map[string]any) []string {
	items, ok := advice["recommendations"].([]any)
	if !ok {
		return nil
	}
	result := make([]string, 0, len(items))
	for _, item := range items {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		severity := strings.ToUpper(fmt.Sprint(row["severity"]))
		action := fmt.Sprint(row["action"])
		reason := fmt.Sprint(row["reason"])
		result = append(result, fmt.Sprintf("[%s] %s — %s", severity, action, reason))
	}
	return result
}
