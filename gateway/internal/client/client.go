package client

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

type Client struct {
	BaseURL string
	APIKey  string
	HTTP    *http.Client
}

type ChatRequest struct {
	Model   string
	Message string
	System  string
}

type ChatResponse struct {
	ID        string
	Content   string
	RequestID string
}

type BackendStatus struct {
	ID      string   `json:"id"`
	URL     string   `json:"url"`
	Enabled bool     `json:"enabled"`
	Healthy bool     `json:"healthy"`
	Weight  int      `json:"weight"`
	Models  []string `json:"models"`
}

type APIError struct {
	StatusCode int    `json:"status_code"`
	Code       string `json:"code"`
	Message    string `json:"message"`
	RequestID  string `json:"request_id"`
}

func (err *APIError) Error() string {
	if err.Code == "" {
		return fmt.Sprintf("gateway returned HTTP %d: %s", err.StatusCode, err.Message)
	}
	return fmt.Sprintf("gateway returned HTTP %d (%s): %s", err.StatusCode, err.Code, err.Message)
}

func New(baseURL string, apiKey string) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), APIKey: apiKey, HTTP: http.DefaultClient}
}

func (client *Client) Chat(ctx context.Context, request ChatRequest) (ChatResponse, error) {
	payload, err := json.Marshal(chatPayload(request, false))
	if err != nil {
		return ChatResponse{}, err
	}
	response, err := client.do(ctx, payload)
	if err != nil {
		return ChatResponse{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ChatResponse{}, decodeAPIError(response)
	}
	var payloadResponse struct {
		ID      string `json:"id"`
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payloadResponse); err != nil {
		return ChatResponse{}, fmt.Errorf("decode chat response: %w", err)
	}
	if len(payloadResponse.Choices) == 0 {
		return ChatResponse{}, errors.New("chat response contains no choices")
	}
	return ChatResponse{ID: payloadResponse.ID, Content: payloadResponse.Choices[0].Message.Content, RequestID: response.Header.Get("X-Request-ID")}, nil
}

func (client *Client) StreamChat(ctx context.Context, request ChatRequest, onChunk func(string) error) (ChatResponse, error) {
	payload, err := json.Marshal(chatPayload(request, true))
	if err != nil {
		return ChatResponse{}, err
	}
	response, err := client.do(ctx, payload)
	if err != nil {
		return ChatResponse{}, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ChatResponse{}, decodeAPIError(response)
	}
	var content strings.Builder
	var responseID string
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 4096), 1<<20)
	done := false
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if data == "[DONE]" {
			done = true
			break
		}
		var event struct {
			ID      string `json:"id"`
			Choices []struct {
				Delta struct {
					Content string `json:"content"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(data), &event); err != nil {
			return ChatResponse{}, fmt.Errorf("decode stream event: %w", err)
		}
		if event.ID != "" {
			responseID = event.ID
		}
		if len(event.Choices) == 0 || event.Choices[0].Delta.Content == "" {
			continue
		}
		chunk := event.Choices[0].Delta.Content
		content.WriteString(chunk)
		if onChunk != nil {
			if err := onChunk(chunk); err != nil {
				return ChatResponse{}, err
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return ChatResponse{}, fmt.Errorf("read stream: %w", err)
	}
	if !done {
		return ChatResponse{}, errors.New("stream ended before [DONE]")
	}
	return ChatResponse{ID: responseID, Content: content.String(), RequestID: response.Header.Get("X-Request-ID")}, nil
}

func (client *Client) do(ctx context.Context, payload []byte) (*http.Response, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.BaseURL+"/v1/chat/completions", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	if client.APIKey != "" {
		request.Header.Set("Authorization", "Bearer "+client.APIKey)
	}
	httpClient := client.HTTP
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return httpClient.Do(request)
}

func (client *Client) Doctor(ctx context.Context) (map[string]any, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, client.BaseURL+"/healthz", nil)
	if err != nil {
		return nil, err
	}
	response, err := client.HTTP.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode health response: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &APIError{StatusCode: response.StatusCode, Message: "health check failed"}
	}
	return payload, nil
}

func (client *Client) Backends(ctx context.Context) ([]BackendStatus, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, client.BaseURL+"/api/backends", nil)
	if err != nil {
		return nil, err
	}
	response, err := client.HTTP.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &APIError{StatusCode: response.StatusCode, Message: response.Status}
	}
	var payload struct {
		Backends []BackendStatus `json:"backends"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode backend status: %w", err)
	}
	return payload.Backends, nil
}

func (client *Client) Advice(ctx context.Context) (map[string]any, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, client.BaseURL+"/advice", nil)
	if err != nil {
		return nil, err
	}
	response, err := client.HTTP.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode tuning advice: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &APIError{StatusCode: response.StatusCode, Message: "tuning advice unavailable"}
	}
	return payload, nil
}

func chatPayload(request ChatRequest, stream bool) map[string]any {
	messages := make([]map[string]string, 0, 2)
	if request.System != "" {
		messages = append(messages, map[string]string{"role": "system", "content": request.System})
	}
	messages = append(messages, map[string]string{"role": "user", "content": request.Message})
	return map[string]any{"model": request.Model, "messages": messages, "stream": stream}
}

func decodeAPIError(response *http.Response) error {
	var payload struct {
		Error struct {
			Code      string `json:"code"`
			Message   string `json:"message"`
			RequestID string `json:"request_id"`
		} `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(&payload); err != nil {
		return &APIError{StatusCode: response.StatusCode, Message: response.Status}
	}
	return &APIError{StatusCode: response.StatusCode, Code: payload.Error.Code, Message: payload.Error.Message, RequestID: payload.Error.RequestID}
}
