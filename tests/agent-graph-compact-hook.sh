#!/usr/bin/env bash
# Test the SessionStart (compact) hook with a stubbed herdr; no live herdr calls.
# The hook must print JSON only for a lead pane and stay silent (exit 0) otherwise.
#
# Fixture scope: herdr is a stub that prints $STUB_DIR/list.json or fails.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/plugins/agent-graph/hooks/after-compact.sh"
HOOKS_JSON="$ROOT/plugins/agent-graph/hooks/hooks.json"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-graph-compact.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT

PASS=0; FAIL=0
ok()     { PASS=$((PASS+1)); printf 'ok %s\n' "$1"; }
not_ok() { FAIL=$((FAIL+1)); printf 'not ok %s\n' "$1" >&2; }
expect() { local name="$1"; shift; if "$@"; then ok "$name"; else not_ok "$name"; fi; }

command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

BIN="$TMP/bin"; mkdir -p "$BIN"
cat >"$BIN/herdr" <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$STUB_DIR/herdr.log"
[[ "$1 $2" == "agent list" ]] || exit 0
[[ ! -f "$STUB_DIR/herdr.fail" ]] || exit 1
cat "$STUB_DIR/list.json"
STUB
chmod +x "$BIN/herdr"

STUB_DIR="$TMP/stub"; STATE="$TMP/state"
mkdir -p "$STUB_DIR" "$STATE"
export STUB_DIR AGENT_GRAPH_STATE_ROOT="$STATE"

# $1=agent name ("" for none). The own pane is w1:p1; another pane is w1:p2.
set_list() {
  local own
  if [[ -n "$1" ]]; then own="{\"name\":\"$1\",\"pane_id\":\"w1:p1\",\"workspace_id\":\"w1\"}"
  else own='{"pane_id":"w1:p1","workspace_id":"w1"}'; fi
  printf '{"result":{"agents":[%s,{"name":"lead-other","pane_id":"w1:p2","workspace_id":"w1"}]}}\n' "$own" >"$STUB_DIR/list.json"
}

# Run the hook. Env: HERDR_ENV defaults to 1; set RUN_NOENV=1 to unset it.
run_hook() {
  OUT_FILE="$TMP/out"; : >"$STUB_DIR/herdr.log"
  if [[ "${RUN_NOENV:-}" == 1 ]]; then
    env -u HERDR_ENV PATH="$BIN:$PATH" HERDR_PANE_ID=w1:p1 HERDR_WORKSPACE_ID=w1 bash "$HOOK" <<<'{"transcript_path":"/t/session.jsonl"}' >"$OUT_FILE" 2>"$TMP/err"
  else
    HERDR_ENV=1 PATH="$BIN:$PATH" HERDR_PANE_ID=w1:p1 HERDR_WORKSPACE_ID=w1 bash "$HOOK" <<<'{"transcript_path":"/t/session.jsonl"}' >"$OUT_FILE" 2>"$TMP/err"
  fi
  RC=$?
}
ctx() { jq -r '.hookSpecificOutput.additionalContext' "$OUT_FILE"; }
silent() { [[ $RC -eq 0 && ! -s "$OUT_FILE" ]]; }

# 1. lead pane -> valid JSON with additionalContext
set_list lead-123; run_hook
expect "lead: exits 0" [ $RC -eq 0 ]
expect "lead: hookEventName is SessionStart" \
  [ "$(jq -r '.hookSpecificOutput.hookEventName' "$OUT_FILE" 2>/dev/null)" = SessionStart ]
expect "lead: additionalContext is a non-empty string" \
  [ "$(jq -r '.hookSpecificOutput.additionalContext | type' "$OUT_FILE" 2>/dev/null)" = string ]
expect "lead: names the compaction" bash -c "grep -q 'compacted' <<<\"\$(jq -r .hookSpecificOutput.additionalContext '$OUT_FILE')\""

# 2. worker / unnamed pane -> silent
set_list impl-456; run_hook; expect "worker name: prints nothing, exits 0" silent
set_list ""; run_hook; expect "unnamed agent: prints nothing, exits 0" silent
printf '{"result":{"agents":[{"name":"lead-other","pane_id":"w1:p2","workspace_id":"w1"}]}}\n' >"$STUB_DIR/list.json"
run_hook; expect "pane not in the list: prints nothing, exits 0" silent

# 3. no HERDR_ENV -> silent, herdr never called
set_list lead-123; RUN_NOENV=1 run_hook
expect "no HERDR_ENV: prints nothing, exits 0" silent
expect "no HERDR_ENV: herdr is not called" [ ! -s "$STUB_DIR/herdr.log" ]

# 4. herdr fails / garbage output -> silent
touch "$STUB_DIR/herdr.fail"; run_hook; expect "herdr fails: prints nothing, exits 0" silent
rm -f "$STUB_DIR/herdr.fail"
echo 'not json' >"$STUB_DIR/list.json"; run_hook; expect "herdr prints garbage: prints nothing, exits 0" silent

# 5. handoff documents
set_list lead-123; run_hook
expect "no handoff document: no path is listed" bash -c "! grep -q 'handoff-g' <<<\"\$(jq -r .hookSpecificOutput.additionalContext '$OUT_FILE')\""
mkdir -p "$STATE/w1" "$STATE/w2"
: >"$STATE/w1/handoff-g1.md"; : >"$STATE/w1/handoff-g2.md"; : >"$STATE/w2/handoff-g1.md"
run_hook
C="$(ctx)"
expect "handoff documents: existing paths are listed" \
  bash -c '[[ "$1" == *"'"$STATE"'/w1/handoff-g1.md"* && "$1" == *"'"$STATE"'/w1/handoff-g2.md"* ]]' _ "$C"
expect "handoff documents: another workspace's path is not listed" \
  bash -c '[[ "$1" != *"'"$STATE"'/w2/"* ]]' _ "$C"

# 6. transcript path
expect "transcript_path is included" bash -c '[[ "$1" == *"/t/session.jsonl"* ]]' _ "$C"
HERDR_ENV=1 PATH="$BIN:$PATH" HERDR_PANE_ID=w1:p1 HERDR_WORKSPACE_ID=w1 bash "$HOOK" <<<'' >"$OUT_FILE" 2>/dev/null
expect "empty stdin: still valid JSON, exit 0" [ $? -eq 0 ] 
expect "empty stdin: output parses" jq -e .hookSpecificOutput.additionalContext "$OUT_FILE" >/dev/null

# hooks.json registers the hook for compaction only
expect "hooks.json: SessionStart matcher is compact" \
  [ "$(jq -r '.hooks.SessionStart[0].matcher' "$HOOKS_JSON")" = compact ]
expect "hooks.json: command uses CLAUDE_PLUGIN_ROOT and the script exists" \
  bash -c 'jq -r ".hooks.SessionStart[0].hooks[0].command" "$1" | grep -q "CLAUDE_PLUGIN_ROOT.*hooks/after-compact.sh"' _ "$HOOKS_JSON"
expect "hook script is executable" [ -x "$HOOK" ]

printf '\n# agent-graph-compact-hook: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
