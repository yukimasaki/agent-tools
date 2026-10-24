#!/usr/bin/env bash
# One operation: create a worker tab and confirm receipt of its first brief.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo 'agent-graph start requires Python 3.11 or newer' >&2; exit 2; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start.py" "$@"
