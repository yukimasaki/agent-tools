#!/usr/bin/env bash
# agent-graph SessionStart hook (matcher: compact, Claude Code only).
# After a compaction, tell a lead to check its handoff documents and the session
# transcript before asking the user to repeat earlier decisions.
# Prints nothing and exits 0 unless this pane is a lead (agent name lead-*) in herdr.
# A missing herdr or jq, a failed herdr call, or bad input never blocks the session.
# Input (stdin JSON): transcript_path. Output: hookSpecificOutput.additionalContext.
# Environment:
#   AGENT_GRAPH_STATE_ROOT  Handoff document root (default ~/.local/state/agent-graph)

trap 'exit 0' ERR
set +e

[[ "${HERDR_ENV:-}" == 1 && -n "${HERDR_PANE_ID:-}" ]] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

INPUT="$(cat 2>/dev/null)"
TRANSCRIPT="$(jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)"

if command -v timeout >/dev/null 2>&1; then
  LIST="$(timeout 5 herdr agent list 2>/dev/null)" || exit 0
else
  LIST="$(herdr agent list 2>/dev/null)" || exit 0
fi

ME="$(jq -c --arg p "$HERDR_PANE_ID" '[.result.agents[]? | select(.pane_id == $p)][0] // empty' <<<"$LIST" 2>/dev/null)"
[[ -n "$ME" ]] || exit 0
NAME="$(jq -r '.name // empty' <<<"$ME")"
[[ "$NAME" == lead-* ]] || exit 0
WS="${HERDR_WORKSPACE_ID:-$(jq -r '.workspace_id // empty' <<<"$ME")}"

STATE_ROOT="${AGENT_GRAPH_STATE_ROOT:-$HOME/.local/state/agent-graph}"
HANDOFFS=()
if [[ -n "$WS" ]]; then
  shopt -s nullglob
  HANDOFFS=("$STATE_ROOT/$WS"/handoff-g*.md)
  shopt -u nullglob
fi

MSG="[agent-graph] The conversation was just compacted into a summary, so details of earlier decisions may be missing. Before asking the user to repeat anything that may already have been decided, check these records:"
if ((${#HANDOFFS[@]} > 0)); then
  MSG+=$'\n'"- Handoff documents (read the latest generation first):"
  for f in "${HANDOFFS[@]}"; do MSG+=$'\n'"  - $f"; done
else
  MSG+=$'\n'"- Handoff documents: none found for this workspace."
fi
if [[ -n "$TRANSCRIPT" ]]; then
  MSG+=$'\n'"- Session transcript (JSONL; search it for the decision): $TRANSCRIPT"
fi
MSG+=$'\n'"Ask only about what neither the summary nor these records settle."

jq -n --arg c "$MSG" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}' 2>/dev/null
exit 0
