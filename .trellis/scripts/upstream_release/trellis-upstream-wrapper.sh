#!/usr/bin/env bash
set -euo pipefail

: "${TRELLIS_HARNESS_ROOT:?TRELLIS_HARNESS_ROOT must name the harness root}"

export PYTHONPATH="$TRELLIS_HARNESS_ROOT/.trellis/scripts${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON_BIN:-python3}" -m upstream_release.wrapper \
  --repo-root "$TRELLIS_HARNESS_ROOT" "$@"
