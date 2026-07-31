package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/BokaiGuo-Lincoln/kavora/gateway/internal/client"
)

const version = "0.1.0"

type config struct {
	BaseURL string `json:"base_url"`
	APIKey  string `json:"api_key"`
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" {
		printHelp(stdout)
		return 0
	}
	jsonOutput := false
	if args[0] == "--json" {
		jsonOutput = true
		args = args[1:]
		if len(args) == 0 {
			printHelp(stdout)
			return 0
		}
	}
	if args[0] == "--version" {
		fmt.Fprintln(stdout, version)
		return 0
	}
	command := args[0]
	args = args[1:]
	if len(args) > 0 && args[0] == "--json" {
		jsonOutput = true
		args = args[1:]
	}
	var err error
	switch command {
	case "doctor":
		err = runDoctor(args, stdout, stderr, jsonOutput)
	case "chat":
		err = runChat(args, stdout, stderr, jsonOutput)
	case "backends":
		err = runBackends(args, stdout, stderr, jsonOutput)
	case "advice":
		err = runAdvice(args, stdout, stderr, jsonOutput)
	case "config":
		err = runConfig(args, stdout, stderr, jsonOutput)
	default:
		err = fmt.Errorf("unknown command %q", command)
	}
	if err != nil {
		if jsonOutput {
			_ = json.NewEncoder(stdout).Encode(map[string]any{"error": map[string]string{"message": err.Error()}})
		} else {
			fmt.Fprintln(stderr, "kavora:", err)
		}
		return 1
	}
	return 0
}

func runDoctor(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	flags := flag.NewFlagSet("doctor", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	if err := flags.Parse(args); err != nil {
		return err
	}
	_, keySource, err := loadCredentials("")
	status := map[string]any{"version": version, "base_url": *baseURL, "auth_source": keySource, "reachable": false}
	if err == nil {
		status["auth_available"] = true
	} else {
		status["auth_available"] = false
		status["auth_error"] = err.Error()
	}
	payload, healthErr := client.New(*baseURL, "").Doctor(context.Background())
	if healthErr == nil {
		status["reachable"] = true
		status["health"] = payload
	}
	if jsonOutput {
		return json.NewEncoder(stdout).Encode(status)
	}
	for _, line := range []string{fmt.Sprintf("Kavora %s", version), fmt.Sprintf("Gateway: %s", *baseURL), fmt.Sprintf("Auth: %v (%s)", status["auth_available"], keySource), fmt.Sprintf("Reachable: %v", status["reachable"])} {
		fmt.Fprintln(stdout, line)
	}
	if healthErr != nil {
		return healthErr
	}
	return nil
}

func runChat(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	flags := flag.NewFlagSet("chat", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	apiKey := flags.String("api-key", "", "one-off API key; prefer KAVORA_API_KEY")
	model := flags.String("model", "demo-model", "model name")
	message := flags.String("message", "", "user message")
	system := flags.String("system", "", "optional system prompt")
	stream := flags.Bool("stream", true, "stream response")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*message) == "" {
		return errors.New("--message is required")
	}
	key, _, err := loadCredentials(*apiKey)
	if err != nil {
		return err
	}
	request := client.ChatRequest{Model: *model, Message: *message, System: *system}
	kavora := client.New(*baseURL, key)
	if *stream {
		response, err := kavora.StreamChat(context.Background(), request, func(chunk string) error {
			if !jsonOutput {
				_, _ = io.WriteString(stdout, chunk)
			}
			return nil
		})
		if err != nil {
			return err
		}
		if !jsonOutput {
			fmt.Fprintln(stdout)
			fmt.Fprintf(stderr, "request %s\n", response.RequestID)
			return nil
		}
		return json.NewEncoder(stdout).Encode(response)
	}
	response, err := kavora.Chat(context.Background(), request)
	if err != nil {
		return err
	}
	if jsonOutput {
		return json.NewEncoder(stdout).Encode(response)
	}
	_, _ = fmt.Fprintln(stdout, response.Content)
	fmt.Fprintf(stderr, "request %s\n", response.RequestID)
	return nil
}

func runBackends(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	flags := flag.NewFlagSet("backends", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	if err := flags.Parse(args); err != nil {
		return err
	}
	backends, err := client.New(*baseURL, "").Backends(context.Background())
	if err != nil {
		return err
	}
	if jsonOutput {
		return json.NewEncoder(stdout).Encode(map[string]any{"backends": backends})
	}
	for _, backend := range backends {
		state := "unhealthy"
		if backend.Healthy {
			state = "healthy"
		}
		fmt.Fprintf(stdout, "%-20s %-10s weight=%d %s\n", backend.ID, state, backend.Weight, backend.URL)
	}
	return nil
}

func runAdvice(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	flags := flag.NewFlagSet("advice", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	if err := flags.Parse(args); err != nil {
		return err
	}
	advice, err := client.New(*baseURL, "").Advice(context.Background())
	if err != nil {
		return err
	}
	if jsonOutput {
		return json.NewEncoder(stdout).Encode(advice)
	}
	if recommendations, ok := advice["recommendations"].([]any); ok {
		for _, item := range recommendations {
			if row, ok := item.(map[string]any); ok {
				fmt.Fprintf(stdout, "[%s] %s: %s\n", row["severity"], row["action"], row["reason"])
			}
		}
	} else {
		fmt.Fprintln(stdout, advice)
	}
	return nil
}

func runConfig(args []string, stdout io.Writer, stderr io.Writer, jsonOutput bool) error {
	if len(args) == 0 || args[0] != "init" {
		return errors.New("usage: kavora config init --api-key <key> [--base-url <url>]")
	}
	flags := flag.NewFlagSet("config init", flag.ContinueOnError)
	flags.SetOutput(stderr)
	baseURL := flags.String("base-url", envOr("KAVORA_GATEWAY_URL", "http://127.0.0.1:18000"), "Gateway URL")
	apiKey := flags.String("api-key", "", "API key to store")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *apiKey == "" {
		return errors.New("--api-key is required")
	}
	path, err := configPath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(config{BaseURL: *baseURL, APIKey: *apiKey}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, append(data, '\n'), 0o600); err != nil {
		return err
	}
	if jsonOutput {
		return json.NewEncoder(stdout).Encode(map[string]string{"config_path": path, "base_url": *baseURL})
	}
	fmt.Fprintln(stdout, "Saved", path)
	return nil
}

func loadCredentials(flagValue string) (string, string, error) {
	if flagValue != "" {
		return flagValue, "flag", nil
	}
	if value := os.Getenv("KAVORA_API_KEY"); value != "" {
		return value, "env", nil
	}
	path, err := configPath()
	if err == nil {
		data, readErr := os.ReadFile(path)
		if readErr == nil {
			var stored config
			if json.Unmarshal(data, &stored) == nil && stored.APIKey != "" {
				return stored.APIKey, "config", nil
			}
		}
	}
	return "", "missing", errors.New("API key missing; set KAVORA_API_KEY, run 'kavora config init', or pass --api-key")
}

func configPath() (string, error) {
	directory, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, "kavora", "config.json"), nil
}

func envOr(name string, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func printHelp(writer io.Writer) {
	fmt.Fprintln(writer, `Kavora CLI — operate the Go/Rust AI infrastructure gateway

Usage:
  kavora doctor [--json] [--base-url URL]
  kavora backends [--json] [--base-url URL]
  kavora advice [--json] [--base-url URL]
  kavora chat [--json] --message TEXT [--model NAME] [--stream]
  kavora config init --api-key KEY [--base-url URL]

Environment:
  KAVORA_GATEWAY_URL  Gateway URL (default: http://127.0.0.1:18000)
  KAVORA_API_KEY      API key, preferred over the local config file

Output:
  Human-readable output is default; --json emits stable JSON to stdout.`)
}
