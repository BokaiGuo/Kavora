#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
check_only=false
if [[ "${1:-}" == "--check" ]]; then
  check_only=true
elif [[ $# -gt 0 ]]; then
  printf 'usage: %s [--check]\n' "$0" >&2
  exit 2
fi

required_commands=(protoc protoc-gen-go protoc-gen-go-grpc protoc-gen-prost protoc-gen-tonic gofmt rustfmt)
for command_name in "${required_commands[@]}"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'missing protobuf generator: %s\n' "$command_name" >&2
    exit 1
  fi
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
mkdir -p "$tmp_dir/go" "$tmp_dir/rust"

protoc \
  --proto_path="$repo_root/proto" \
  --go_out="$tmp_dir/go" \
  --go_opt=paths=source_relative \
  --go-grpc_out="$tmp_dir/go" \
  --go-grpc_opt=paths=source_relative \
  --prost_out="$tmp_dir/rust" \
  --prost_opt=boxed=.kavora.policy.v1.StreamCheckRequest.payload.open \
  --tonic_out="$tmp_dir/rust" \
  "$repo_root/proto/policy/v1/policy.proto" \
  "$repo_root/proto/backend/v1/backend_state.proto" \
  "$repo_root/proto/tool/v1/tool.proto"

gofmt -w "$tmp_dir/go"
while IFS= read -r -d '' rust_file; do
  rustfmt --edition 2024 "$rust_file"
done < <(find "$tmp_dir/rust" -type f -name '*.rs' -print0)

if $check_only; then
  diff -ru "$repo_root/proto/gen/go" "$tmp_dir/go"
  diff -ru "$repo_root/proto/gen/rust" "$tmp_dir/rust"
  exit 0
fi

rm -rf "$repo_root/proto/gen/go" "$repo_root/proto/gen/rust"
mkdir -p "$repo_root/proto/gen"
cp -R "$tmp_dir/go" "$repo_root/proto/gen/go"
cp -R "$tmp_dir/rust" "$repo_root/proto/gen/rust"
