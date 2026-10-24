#!/usr/bin/env bash
# Merge policy tests with a fake gh; never writes to GitHub.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEST_DIR"' EXIT
python3 "$ROOT/tests/agent-graph-merge.py" "$ROOT" "$TEST_DIR"
