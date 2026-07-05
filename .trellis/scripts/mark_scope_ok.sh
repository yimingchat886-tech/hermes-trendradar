#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

fingerprint() {
  {
    git diff --cached --binary
    git diff --cached --name-status
    git write-tree
  } | sha256sum | awk '{print $1}'
}

if [[ "${1:-}" == "--print" ]]; then
  fingerprint
  exit 0
fi

mkdir -p .trellis/.runtime
fp="$(fingerprint)"
tree="$(git write-tree)"
{
  printf 'fingerprint=%s\n' "$fp"
  printf 'tree=%s\n' "$tree"
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > .trellis/.runtime/scope-check.ok

printf 'scope-check.ok %s\n' "$fp"
