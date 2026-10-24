#!/usr/bin/env bash
# Local git repositories plus fake gh; never launches a live agent or service.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEST_DIR"' EXIT
python3 "$ROOT/tests/agent-graph-issue.py" "$ROOT" "$TEST_DIR"
