#!/usr/bin/env bash
# Prepare one assigned Issue; the calling skill launches its worker separately.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo 'agent-graph prepare-issue requires Python 3.11 or newer' >&2; exit 2; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/prepare-issue.py" "$@"
