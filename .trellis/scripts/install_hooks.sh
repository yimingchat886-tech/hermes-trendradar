#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

mkdir -p .git/hooks
for hook in .trellis/scripts/hooks/*; do
  [[ -f "$hook" ]] || continue
  cp "$hook" ".git/hooks/$(basename "$hook")"
  chmod +x ".git/hooks/$(basename "$hook")"
  printf 'installed %s\n' ".git/hooks/$(basename "$hook")"
done
