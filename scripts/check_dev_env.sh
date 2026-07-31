#!/usr/bin/env bash
set -euo pipefail

required_commands=(
  go
  gofmt
  rustc
  cargo
  protoc
  protoc-gen-go
  protoc-gen-go-grpc
  protoc-gen-prost
  protoc-gen-tonic
  python3
)

missing_commands=()
for command_name in "${required_commands[@]}"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    missing_commands+=("$command_name")
  fi
done

if ((${#missing_commands[@]} > 0)); then
  printf 'missing required development commands: %s\n' "${missing_commands[*]}" >&2
  printf 'see docs/development.md for installation instructions\n' >&2
  exit 1
fi

printf '%-24s %s\n' "go" "$(go version)"
printf '%-24s %s\n' "rustc" "$(rustc --version)"
printf '%-24s %s\n' "cargo" "$(cargo --version)"
printf '%-24s %s\n' "protoc" "$(protoc --version)"
printf '%-24s %s\n' "protoc-gen-go" "$(protoc-gen-go --version)"
printf '%-24s %s\n' "protoc-gen-go-grpc" "$(protoc-gen-go-grpc --version)"
printf '%-24s %s\n' "protoc-gen-prost" "$(protoc-gen-prost --version)"
printf '%-24s %s\n' "protoc-gen-tonic" "$(protoc-gen-tonic --version)"
printf '%-24s %s\n' "python" "$(python3 --version)"

go_version="$(go env GOVERSION)"
if [[ "$go_version" != "go1.26.5" ]]; then
  printf 'warning: validated Go version is go1.26.5, found %s\n' "$go_version" >&2
fi

printf 'development environment check passed\n'
