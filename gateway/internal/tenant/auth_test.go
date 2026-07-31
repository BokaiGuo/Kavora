package tenant

import "testing"

func TestBearerToken(t *testing.T) {
	tests := []struct {
		header string
		want   string
		ok     bool
	}{
		{header: "Bearer secret", want: "secret", ok: true},
		{header: "bearer secret", want: "secret", ok: true},
		{header: "Bearer", ok: false},
		{header: "Basic secret", ok: false},
		{header: "Bearer secret extra", ok: false},
	}
	for _, test := range tests {
		token, ok := BearerToken(test.header)
		if token != test.want || ok != test.ok {
			t.Fatalf("BearerToken(%q) = %q, %v", test.header, token, ok)
		}
	}
}
