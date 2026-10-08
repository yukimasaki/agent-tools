#!/usr/bin/env bash
# Isolated regression tests; never contacts live herdr or Docker services.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_directory="$(mktemp -d)"
trap 'rm -rf -- "$test_directory"' EXIT
export HOME="$test_directory/home"
export XDG_CONFIG_HOME="$test_directory/config"
export XDG_STATE_HOME="$test_directory/state"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME"
python3 "$ROOT/tests/memgate.py" "$ROOT" "$test_directory"
