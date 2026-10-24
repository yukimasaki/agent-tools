#!/usr/bin/env bash
# One explicit merge request, checked against current approval and required CI.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo 'agent-graph merge-issue requires Python 3.11 or newer' >&2; exit 2; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/merge-issue.py" "$@"
