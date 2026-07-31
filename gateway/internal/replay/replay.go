package replay

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
)

type Event struct {
	Sequence  int             `json:"sequence"`
	Kind      string          `json:"kind"`
	RequestID string          `json:"request_id"`
	Payload   json.RawMessage `json:"payload"`
}
type Recorder struct {
	next   int
	writer io.Writer
}

func NewRecorder(writer io.Writer) *Recorder { return &Recorder{writer: writer} }
func (r *Recorder) Append(kind, requestID string, payload any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	event := Event{Sequence: r.next, Kind: kind, RequestID: requestID, Payload: data}
	r.next++
	encoded, err := json.Marshal(event)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(r.writer, string(encoded))
	return err
}
func ReadAll(reader io.Reader) ([]Event, error) {
	scanner := bufio.NewScanner(reader)
	events := []Event{}
	sequence := 0
	for scanner.Scan() {
		var event Event
		if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
			return nil, err
		}
		if event.Sequence != sequence {
			return nil, fmt.Errorf("non-deterministic sequence: got %d want %d", event.Sequence, sequence)
		}
		events = append(events, event)
		sequence++
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return events, nil
}
