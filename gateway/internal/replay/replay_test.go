package replay

import (
	"bytes"
	"testing"
)

func TestReplaySequence(t *testing.T) {
	var b bytes.Buffer
	r := NewRecorder(&b)
	if err := r.Append("request", "r1", map[string]string{"x": "y"}); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadAll(&b); err != nil {
		t.Fatal(err)
	}
}
