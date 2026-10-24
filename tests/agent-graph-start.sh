#!/usr/bin/env bash
# Isolated startup tests: never calls a live herdr or a model.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEST_DIR"' EXIT
python3 "$ROOT/tests/agent-graph-start.py" "$ROOT" "$TEST_DIR"
