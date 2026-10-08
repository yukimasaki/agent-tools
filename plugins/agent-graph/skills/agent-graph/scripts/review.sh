#!/usr/bin/env bash
# agent-graph review — review a branch diff with Codex and print the output path.
# Usage: review.sh [--uncommitted] [--base <branch>] [--out <file>]
# Criteria, model, and effort use Codex defaults and built-in review-agent.
# For a focused review invoke codex exec review "<target and criteria>" directly;
# --base cannot be combined with a prompt.
set -uo pipefail

die() { echo "agent-graph review: $*" >&2; exit 1; }

UNCOMMITTED=false; OUT=""; BASE_BRANCH="main"; BASE_SET=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uncommitted) UNCOMMITTED=true; shift ;;
    --base)
      [[ $# -ge 2 ]] || die "--base requires a value"
      BASE_BRANCH="$2"; BASE_SET=true; shift 2 ;;
    --out)
      [[ $# -ge 2 ]] || die "--out requires a value"
      OUT="$2"; shift 2 ;;
    -h|--help) echo "Usage: review.sh [--uncommitted] [--base <branch>] [--out <file>]"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

$UNCOMMITTED && $BASE_SET && die "--uncommitted and --base cannot be combined (--base would be ignored)"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "run inside a git worktree"
command -v codex >/dev/null 2>&1 || die "codex not found"

if [[ -z "$OUT" ]]; then
  STATE_ROOT="${AGENT_GRAPH_STATE_ROOT:-$HOME/.local/state/agent-graph}"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached)"
  OUT="$STATE_ROOT/reviews/${BRANCH//\//-}-$(date +%Y%m%d-%H%M%S).md"
fi
mkdir -p "$(dirname "$OUT")" || die "cannot create output directory: $OUT"
# Do not return stale output when Codex exits successfully without writing.
rm -f -- "$OUT"

if $UNCOMMITTED; then
  codex exec review --uncommitted -o "$OUT" >&2 || die "codex exec review failed"
else
  git fetch origin "$BASE_BRANCH" >&2 || die "git fetch origin $BASE_BRANCH failed; use --uncommitted or check connectivity"
  BASE_SHA="$(git merge-base "origin/$BASE_BRANCH" HEAD 2>/dev/null)" || die "cannot resolve merge-base with origin/$BASE_BRANCH"
  [[ -n "$BASE_SHA" ]] || die "cannot resolve merge-base with origin/$BASE_BRANCH"
  echo "base: $BASE_SHA" >&2
  codex exec review --base "$BASE_SHA" -o "$OUT" >&2 || die "codex exec review failed"
fi

[[ -s "$OUT" ]] || die "review output is empty: $OUT"
echo "$OUT"
