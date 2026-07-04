#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: .trellis/scripts/trellis_pr.sh <task-dir> [--title <title>] [--ack-deletions] [--ack-migrations] [--reviewed <conclusion>]
EOF
}

task_dir="${1:-}"
if [[ "$task_dir" == "-h" || "$task_dir" == "--help" ]]; then
  usage
  exit 0
fi
if [[ -z "$task_dir" ]]; then
  usage >&2
  exit 1
fi
shift

ack_deletions=0
ack_migrations=0
reviewed=""
title=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ack-deletions)
      ack_deletions=1
      shift
      ;;
    --ack-migrations)
      ack_migrations=1
      shift
      ;;
    --reviewed)
      if [[ $# -lt 2 || -z "$2" ]]; then
        printf '%s\n' '--reviewed requires a conclusion' >&2
        exit 2
      fi
      reviewed="$2"
      shift 2
      ;;
    --title)
      if [[ $# -lt 2 || -z "$2" ]]; then
        printf '%s\n' '--title requires a title' >&2
        exit 2
      fi
      title="$2"
      shift 2
      ;;
    *) printf 'unknown arg: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

task_json="$task_dir/task.json"
if [[ ! -f "$task_json" ]]; then
  printf 'task.json not found: %s\n' "$task_json" >&2
  exit 1
fi

slug="$(python3 - "$task_json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("name", ""))
PY
)"
if [[ -z "$slug" ]]; then
  printf 'task slug missing in %s\n' "$task_json" >&2
  exit 1
fi

base="$(git merge-base HEAD main)"
range="$base..HEAD"
tmp_index="$(mktemp)"
trap 'rm -f "$tmp_index"' EXIT
GIT_INDEX_FILE="$tmp_index" git read-tree HEAD

printf 'BASE %s\n' "$base"
printf 'FILES\n'
mapfile -t files < <(git diff --name-only "$range")
printf '%s\n' "${files[@]}"

deleted_lines="$(
  git diff --numstat "$range" |
  awk '{ if ($2 ~ /^[0-9]+$/) deleted += $2 } END { print deleted + 0 }'
)"
if [[ "$deleted_lines" -gt 0 && "$ack_deletions" != "1" ]]; then
  printf 'delete review required: %s deleted lines. Re-run with --ack-deletions after review.\n' "$deleted_lines" >&2
  git diff --name-status "$range" >&2
  exit 2
fi

touches_migration=0
if printf '%s\n' "${files[@]}" | grep -Eiq '(^|/)migrations?/|schema'; then
  touches_migration=1
  if [[ "$ack_migrations" != "1" ]]; then
    printf 'migration/schema review required. Re-run with --ack-migrations after review.\n' >&2
    exit 2
  fi
fi

high_risk=0
if printf '%s\n' "${files[@]}" | grep -Eiq '(^|/)(migrations?|workflows?|common)/|schema|public[_-]?api'; then
  high_risk=1
fi
if [[ "$high_risk" == "1" && -z "$reviewed" ]]; then
  cat >&2 <<EOF
rescue review required:
- Task: $slug
- Base: $base
- Check for missed requirements, over-scope, deleted behavior, migrations/schema/common/workflow risk.
Re-run with --reviewed "<conclusion>" when accepted.
EOF
  exit 2
fi

mapfile -t py_files < <(printf '%s\n' "${files[@]}" | grep -E '\.py$' || true)
mapfile -t existing_py < <(for path in "${py_files[@]}"; do [[ -f "$path" ]] && printf '%s\n' "$path"; done)
if [[ ${#existing_py[@]} -gt 0 ]]; then
  python3 -m py_compile "${existing_py[@]}"
fi
if [[ -f package.json ]]; then
  npm run lint --if-present
  npm run typecheck --if-present
fi

python3 - "$task_json" "${files[@]}" <<'PY'
import fnmatch, json, sys
task = json.load(open(sys.argv[1], encoding="utf-8"))
patterns = task.get("touches") or []
files = sys.argv[2:]
outside = [
    path for path in files
    if patterns and not any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
]
if outside:
    print("OUT_OF_SCOPE")
    for path in outside:
        print(path)
PY

check_title="${title:-$(git log -1 --format=%s)}"
if [[ "$check_title" != *"[$slug]"* ]]; then
  printf 'title must include [%s]: %s\n' "$slug" "$check_title" >&2
  exit 2
fi

mkdir -p .trellis/.runtime
printf 'base=%s\nreviewed=%s\n' "$base" "$reviewed" > ".trellis/.runtime/pr-ok-$slug"
printf 'trellis_pr ok: %s\n' "$slug"
