#!/usr/bin/env bash
# Test watcher decisions round by round with a stubbed herdr; no live herdr calls.
# The first list read uses list.0.json, falling back to list.1.json. Later reads
# use list.<N>.json or list.last.json. Pane tails use read.<pane>[.<call-count>].
# Most tests use FOREGROUND=1, bounded MAX_ROUNDS, and INTERVAL=0. Launch tests
# exercise detached mode using only test-owned PID files and processes.
# External screen fixtures, including Japanese pane text, remain untranslated.
#
# Fixture scope:
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCH="$ROOT/plugins/agent-graph/skills/agent-graph/scripts/watch.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-graph-watch.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT

PASS=0; FAIL=0
ok()     { PASS=$((PASS+1)); printf 'ok %s\n' "$1"; }
not_ok() { FAIL=$((FAIL+1)); printf 'not ok %s\n' "$1" >&2; }
expect() { local name="$1"; shift; if "$@"; then ok "$name"; else not_ok "$name"; fi; }

command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

BIN="$TMP/bin"; mkdir -p "$BIN"
cat >"$BIN/herdr" <<'STUB'
#!/usr/bin/env bash
S="$STUB_DIR"
case "$1 $2" in
  "agent list")
    n=$(( $(cat "$S/calls" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$S/calls"
    f="$S/list.$((n-1)).json"
    [[ -f "$f" ]] || { [[ $n -eq 1 ]] && f="$S/list.1.json" || f="$S/list.last.json"; }
    [[ -f "$f" ]] || exit 1
    [[ "$(cat "$f")" == FAIL ]] && exit 1
    cat "$f" ;;
  "agent read")
    k="${3//:/_}"; n=$(cat "$S/calls" 2>/dev/null || echo 0)
    printf '%s\n' "$3" >>"$S/read.targets"
    [[ ! -f "$S/read.fail.$k.$n" ]] || exit 1
    if [[ "$k" == lead-9 && -f "$S/composed" && -f "$S/question.after-compose" ]]; then
      cat "$S/question.after-compose"; exit 0
    fi
    if [[ -f "$S/read.$k.$n" ]]; then cat "$S/read.$k.$n"; else cat "$S/read.$k" 2>/dev/null || true; fi ;;
  "agent prompt") (cat "$S/calls" 2>/dev/null || echo 0) >>"$S/prompt.rounds"; echo "$3" >>"$S/prompt.targets"; printf '%s\n=====\n' "$4" >>"$S/prompts.log"; [[ -f "$S/prompt.fail" ]] && exit 1; exit 0 ;;
  "pane get") [[ -f "$S/pane.alive.${3//:/_}" ]]; exit $? ;;
  "pane read") cat "$S/read.${3//:/_}" 2>/dev/null || true ;;
  "tab get") touch "$S/composed"; printf '{"result":{"tab":{"label":"%s"}}}\n' "$(cat "$S/label" 2>/dev/null || echo tab)" ;;
  "notification show") printf '%s\n' "$*" >>"$S/notify.log"; [[ -f "$S/notify.fail" ]] && exit 1; exit 0 ;;
  "workspace get")
    n=$(cat "$S/calls" 2>/dev/null || echo 0)
    if [[ -f "$S/ws.gone.from" && $n -ge $(cat "$S/ws.gone.from") ]]; then
      echo '{"error":{"code":"workspace_not_found","message":"workspace not found"}}'; exit 1
    fi
    echo '{"result":{"workspace":{}}}' ;;
  *) exit 0 ;;
esac
STUB
chmod +x "$BIN/herdr"
cat >"$BIN/date" <<'STUB'
#!/usr/bin/env bash
# If clock exists, date +%s returns clock + round count * 100 to simulate time.
if [[ "${1:-}" == +%s && -f "${STUB_DIR:-/nonexistent}/clock" ]]; then
  echo $(( $(cat "$STUB_DIR/clock") + $(cat "$STUB_DIR/calls" 2>/dev/null || echo 0) * 100 ))
else
  exec /bin/date "$@"
fi
STUB
chmod +x "$BIN/date"

# $1=case name. Subsequent helpers use $STUB / $STATE.
new_case() {
  STUB="$TMP/$1.stub"; STATE="$TMP/$1.state"; mkdir -p "$STUB" "$STATE"
}

# mklist <N|last> <lead-status> [pane|name|tab|status|seq[|completion_seq] ...]
# A - name is unnamed; default workspace is w1; prefix w2: selects workspace w2.
mklist() {
  local n="$1" lead="$2"; shift 2
  local items=() row ws pane name tab st seq
  items+=("$(jq -nc --arg s "$lead" '{agent:"claude",agent_status:$s,name:"lead-9",pane_id:"w1:p0",tab_id:"w1:t0",workspace_id:"w1",state_change_seq:1}')")
  for row in "$@"; do
    ws=w1; [[ "$row" == w2:* ]] && { ws=w2; row="${row#w2:}"; }
    IFS='|' read -r pane name tab st seq comp <<<"$row"
    items+=("$(jq -nc --arg p "$pane" --arg n "$name" --arg t "$tab" --arg s "$st" --arg q "$seq" --arg w "$ws" --arg c "${comp:-}" \
      '{agent:"claude",agent_status:$s,pane_id:$p,tab_id:$t,workspace_id:$w}
       + (if $n == "-" then {} else {name:$n} end)
       + (if $q == "" then {} else {state_change_seq:($q|tonumber)} end)
       + (if $c == "" then {} else {completion_seq:($c|tonumber)} end)')")
  done
  printf '%s\n' "${items[@]}" | jq -sc '{id:"x",result:{agents:.,type:"agent_list"}}' >"$STUB/list.$n.json"
}

# $1=number of polling rounds.
run_watch() {
  STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_FOREGROUND=1 \
    AGENT_GRAPH_WATCH_INTERVAL=0 AGENT_GRAPH_WATCH_MAX_ROUNDS="$1" bash "$WATCH" lead-9 >"$STATE/out.txt" 2>&1
}
prompts() { cat "$STUB/prompts.log" 2>/dev/null || true; }
prompt_count() { [[ -f "$STUB/prompts.log" ]] && grep -c '^=====$' "$STUB/prompts.log" || echo 0; }
log() { cat "$STATE/w1/watch.log" 2>/dev/null || true; }

FOOT="  ⏵⏵ bypass permissions on · 1 shell · ← 5 agents"
MARK=" Do you want to proceed?"

# --- Initial observation ---
new_case first
mklist 1 idle "w1:p1|impl-9|w1:t1|done|5" "w1:p2|impl-8|w1:t2|idle|3"
run_watch 2
expect "Does not wake workers already done or idle at startup" test "$(prompt_count)" = 0
expect "Does not notify humans about initial done or idle workers" test ! -s "$STUB/notify.log"

new_case firstblocked
printf '%s\n' "$MARK" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 1
expect "Wakes a blocked worker with confirmation markers on the first round" test "$(prompt_count)" = 1

new_case firstblocked2
printf '1. A\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 1
expect "Does not wake initial blocked without markers on the first round" test "$(prompt_count)" = 0
rm -f "$STUB/calls"; rm -rf "$STATE/w1"
run_watch 2
expect "Wakes unmarked blocked after two observations" test "$(prompt_count)" = 1

# --- done / idle ---
new_case wake
echo "実装ブランチ" >"$STUB/label"
printf '報告をファイルに書きました\n/tmp/report.md\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
body="$(prompts)"
expect "Wakes the lead once for working -> done" test "$(prompt_count)" = 1
expect "Includes name, pane, tab, state, tail, and report-reading instructions" bash -c '[[ "$1" == *impl-9* && "$1" == *w1:p1* && "$1" == *実装ブランチ* && "$1" == *done* && "$1" == */tmp/report.md* && "$1" == *"Read the report file if present; otherwise inspect the pane and respond."* ]]' _ "$body"
expect "Classifies as stopped rather than completed" bash -c '[[ "$1" == *stopped* && "$1" != *completed* ]]' _ "$body"
expect "Human notice includes the lead name and sound done" grep -q -- "notification show lead-9: impl-9 is done --sound done" "$STUB/notify.log"

new_case doneidle
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|idle|6"
mklist 4 idle "w1:p1|impl-9|w1:t1|idle|6"
run_watch 4
expect "Does not notify twice for done -> idle with the same seq" test "$(prompt_count)" = 1

new_case newstop
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|working|7"
mklist 4 idle "w1:p1|impl-9|w1:t1|done|8"
run_watch 4
expect "Notifies a new stop with a changed seq" test "$(prompt_count)" = 2

new_case roundtrip
mklist 1 idle "w1:p1|impl-9|w1:t1|done|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|9"
run_watch 2
expect "Notifies changed done seq when working was between observations" test "$(prompt_count)" = 1

# --- Background shells ---
new_case shellwait
printf '%s\n' "$FOOT" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 3
expect "Does not wake while a shell footer shows background work" test "$(prompt_count)" = 0
expect "Does not notify humans while waiting for a shell" test ! -s "$STUB/notify.log"

new_case shellend
printf '%s\n' "$FOOT" >"$STUB/read.w1_p1"
printf '✻ done · 1 shell still running\nシェルは終わった\n' >"$STUB/read.w1_p1.4"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 3
expect "Wakes the same seq when the shell footer disappears despite historical still running" test "$(prompt_count)" = 1
expect "Uses the updated pane tail in the notice" bash -c '[[ "$1" == *シェルは終わった* ]]' _ "$(prompts)"

new_case shellseq
printf '%s\n' "$FOOT" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
for n in 2 3 4 5 6 7; do
  if (( n % 2 == 0 )); then mklist $n idle "w1:p1|impl-9|w1:t1|done|$n"; else mklist $n idle "w1:p1|impl-9|w1:t1|working|$n"; fi
done
run_watch 7
expect "Does not wake workers waiting for successive background shells" test "$(prompt_count)" = 0

# --- blocked ---
new_case blocked
printf '%s\n' "$MARK" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|done|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 2
expect "Wakes done -> blocked with markers immediately" test "$(prompt_count)" = 1
expect "Classifies permission requests" bash -c '[[ "$1" == *"permission request"* ]]' _ "$(prompts)"
expect "Uses sound request for blocked" grep -q -- "--sound request" "$STUB/notify.log"

new_case ask
printf '1. A\n2. B\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 2
expect "Does not wake unmarked blocked on its first observation" test "$(prompt_count)" = 0
rm -f "$STUB/calls"; rm -rf "$STATE/w1"
run_watch 3
expect "Wakes unmarked blocked after two observations" test "$(prompt_count)" = 1
expect "Classifies other blocked as awaiting confirmation" bash -c '[[ "$1" == *"awaiting confirmation"* ]]' _ "$(prompts)"

new_case transient
printf '● Running hook…\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|working|7"
mklist 4 idle "w1:p1|impl-9|w1:t1|working|7"
run_watch 4
expect "Does not wake transient blocked that returns to working" test "$(prompt_count)" = 0
expect "Does not notify humans about transient blocked" test ! -s "$STUB/notify.log"

new_case blockedtwice
printf '%s\n' "$MARK" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 4 idle "w1:p1|impl-9|w1:t1|blocked|9"
run_watch 4
expect "Does not repeat blocked notices but notifies reentered blocked with a new seq" test "$(prompt_count)" = 2

# --- Transient blocked and shell-wait regressions ---
new_case probe1
printf '作業を終えました\n' >"$STUB/read.w1_p1"
printf '  running hook...\n' >"$STUB/read.w1_p1.3"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|blocked|8"
mklist 4 idle "w1:p1|impl-9|w1:t1|blocked|8"
run_watch 3
expect "Does not classify transient blocked during completion waiting as awaiting confirmation" bash -c '[[ "$2" -le 1 && "$1" != *"awaiting confirmation"* ]]' _ "$(prompts)" "$(prompt_count)"

new_case probe3
printf '%s\n' "$MARK" >"$STUB/read.w1_p1.3"
printf '%s\n' "$FOOT" >"$STUB/read.w1_p1"
printf '%s\n' "$FOOT" >"$STUB/read.w1_p1.5"
printf '指示を待っています\n' >"$STUB/read.w1_p1.6"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 3 idle "w1:p1|impl-9|w1:t1|idle|7"
mklist 4 idle "w1:p1|impl-9|w1:t1|idle|7"
mklist 5 idle "w1:p1|impl-9|w1:t1|idle|7"
run_watch 5
expect "Wakes rejected-confirmation idle after its background shell disappears" bash -c '[[ "$2" = 1 && "$1" == *指示を待っています* ]]' _ "$(prompts)" "$(prompt_count)"

# --- Lead question screens, including named sessions ---
QUESTION='  3. Type something.
───────────────────────────────────────────────
  4. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
─────────────────────────────────────── lead-9 ─'
for mark in select confirm unnamed escape; do
  new_case "leadquestion-$mark"
  case "$mark" in
    select) printf '%s\n' "$QUESTION" ;;
    confirm) printf '%s\n' "${QUESTION/Enter to select/Enter to confirm}" ;;
    unnamed) printf 'Enter to select · ↑/↓ to navigate · Esc to cancel\n' ;;
    escape) printf '  Esc to cancel · Tab to amend · ctrl+e to explain\n' ;;
  esac >"$STUB/read.lead-9"
  mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
  mklist 3 done "w1:p1|impl-9|w1:t1|done|6"
  mklist 4 done "w1:p1|impl-9|w1:t1|done|6"
  run_watch 2
  expect "Defers for a $mark question screen even when lead is done" test "$(prompt_count)" = 0
  expect "Logs question-screen deferral ($mark)" bash -c 'grep -q "question screen; defer 1 notices" "$1"' _ "$STATE/w1/watch.log"
  # agent list is also read at startup, so round three reads at calls=4.
  printf '❯ 次の指示を待っています\n' >"$STUB/read.lead-9.4"
  rm -f "$STUB/calls"; rm -rf "$STATE/w1"
  run_watch 4
  expect "Sends the same seq once after the question closes ($mark)" test "$(prompt_count)" = 1
  expect "Sends in round three after question closure ($mark)" bash -c '[[ "$(cat "$1")" == 4 ]]' _ "$STUB/prompt.rounds"
done

new_case leaddone
printf '❯ 次の指示を待っています\n' >"$STUB/read.lead-9"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Sends when lead is done without a question screen" test "$(prompt_count)" = 1

for mark in 'Enter to select' 'Enter to confirm'; do
  new_case "leadpartial-${mark// /-}"
  printf '%s\n' "$mark" >"$STUB/read.lead-9"
  mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
  run_watch 2
  expect "Sends when only one selection instruction is present ($mark)" test "$(prompt_count)" = 1
done

new_case leadoldquestion
printf '%s\n' "$QUESTION" "$MARK" >"$STUB/read.lead-9"
for n in {1..15}; do printf '過去の質問を閉じた後の行 %s\n' "$n"; done >>"$STUB/read.lead-9"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Ignores historical question markers far from the screen bottom" test "$(prompt_count)" = 1

# Prose describing instructions must not suppress delivery indefinitely.
for prose in separate same permission rule split; do
  new_case "leadprose-$prose"
  case "$prose" in
    separate) printf '選択の操作案内には Esc to cancel がある。\n確定には Enter to select を使う。\n' ;;
    same) printf '選択の操作案内 (Esc to cancel と Enter to select) を検出する。\n' ;;
    permission) printf '実行許可の画面には Do you want to proceed? と表示される。\n' ;;
    rule) printf 'Permission rule の文字列について説明する。\n' ;;
    split) printf '  Enter to select\n説明文の中に Esc to cancel がある。\n' ;;
  esac >"$STUB/read.lead-9"
  mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
  mklist 3 done "w1:p1|impl-9|w1:t1|done|6"
  run_watch 3
  expect "Sends once when a done lead only describes instructions ($prose)" test "$(prompt_count)" = 1
done

new_case leadindented
printf '  Enter to select · ↑/↓ to navigate · Esc to cancel\n──── lead-9 ────\n' >"$STUB/read.lead-9"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Defers for selection instructions with leading whitespace" test "$(prompt_count)" = 0

# Copied screens may remain in chat, but are above the last three idle lines.
for copied in permission escape select; do
  new_case "leadcopied-$copied"
  case "$copied" in
    permission) printf '    Do you want to proceed?\n' ;;
    escape) printf '    Esc to cancel · Tab to amend · ctrl+e to explain\n' ;;
    select) printf '    Enter to select · ↑/↓ to navigate · Esc to cancel\n' ;;
  esac >"$STUB/read.lead-9"
  printf '\n───────────────────────────────\n❯ \n  ⏵⏵ bypass permissions on\n\n' >>"$STUB/read.lead-9"
  mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
  mklist 3 done "w1:p1|impl-9|w1:t1|done|6"
  run_watch 3
  expect "Sends once with a copied $copied screen above the idle footer" test "$(prompt_count)" = 1
done

new_case leadblankfooter
printf '%s\n' "$QUESTION" >"$STUB/read.lead-9"
printf '\n \n\t\n\n' >>"$STUB/read.lead-9"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Ignores trailing blank lines when detecting instructions" test "$(prompt_count)" = 0

for invalid in selectSuffix confirmSuffix cancelSuffix permission; do
  new_case "leadfooter-$invalid"
  case "$invalid" in
    selectSuffix) printf 'Enter to selected · Esc to cancel\n' ;;
    confirmSuffix) printf 'Enter to confirmation · Esc to cancel\n' ;;
    cancelSuffix) printf 'Esc to cancelled\n' ;;
    permission) printf 'Do you want to proceed?\n' ;;
  esac >"$STUB/read.lead-9"
  mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
  run_watch 2
  expect "Sends for $invalid that does not match footer markers" test "$(prompt_count)" = 1
done

for pane_value in empty null; do
  new_case "leadpane-$pane_value"
  printf '%s\n' "$QUESTION" >"$STUB/read.lead-9"
  mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
  mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
  for n in 1 2; do
    jq -c --arg v "$pane_value" '.result.agents|=map(if .name=="lead-9" then .pane_id=(if $v=="empty" then "" else null end) else . end)' "$STUB/list.$n.json" >"$STUB/list.tmp"
    mv "$STUB/list.tmp" "$STUB/list.$n.json"
  done
  run_watch 2
  expect "Reads by name and defers despite a $pane_value lead pane ID" test "$(prompt_count)" = 0
  expect "Uses the lead name for a $pane_value pane ID" bash -c 'grep -qx "lead-9" "$1"' _ "$STUB/read.targets"
done

# Check the lead screen after composing the notice.
new_case leadcomposequestion
printf '❯ 次の指示を待っています\n' >"$STUB/read.lead-9"
printf '%s\n' "$QUESTION" >"$STUB/question.after-compose"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Defers if the lead asks a question while the notice is composed" test "$(prompt_count)" = 0
expect "Logs deferral for a question detected after composing" bash -c 'grep -q "question screen; defer" "$1"' _ "$STATE/w1/watch.log"

new_case leadreadfail
printf '❯ 次の指示を待っています\n' >"$STUB/read.lead-9"
touch "$STUB/read.fail.lead-9.3"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
mklist 3 done "w1:p1|impl-9|w1:t1|done|6"
mklist 4 done "w1:p1|impl-9|w1:t1|done|6"
run_watch 4
expect "Defers a failed lead read and sends once after the next successful read" bash -c '[[ "$(cat "$1/prompt.rounds")" == 4 && "$(grep -c "^=====$" "$1/prompts.log")" == 1 ]]' _ "$STUB"
expect "Logs lead read failure and deferral" bash -c 'grep -q "cannot read lead screen; defer 1 notices" "$1"' _ "$STATE/w1/watch.log"

new_case leademptystatus
printf '%s\n' "$QUESTION" >"$STUB/read.lead-9"
mklist 1 '' "w1:p1|impl-9|w1:t1|working|5"
mklist 2 '' "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Resolves name and Claude Code kind with an empty status and defers questions" test "$(prompt_count)" = 0
expect "Reads the lead by name despite an empty status" bash -c 'grep -qx "lead-9" "$1"' _ "$STUB/read.targets"

new_case promptnocalls
err="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" herdr agent prompt lead-9 fixture 2>&1)"
expect "Prompt stub emits no stderr when calls is absent" test -z "$err"
expect "Prompt stub records round zero when calls is absent" test "$(cat "$STUB/prompt.rounds")" = 0

new_case leadpi
printf '%s\n' "$QUESTION" >"$STUB/read.lead-9"
mklist 1 done "w1:p1|impl-9|w1:t1|working|5"
mklist 2 done "w1:p1|impl-9|w1:t1|done|6"
for n in 1 2; do
  jq -c '.result.agents|=map(if .name=="lead-9" then .agent="pi" else . end)' "$STUB/list.$n.json" >"$STUB/list.tmp"
  mv "$STUB/list.tmp" "$STUB/list.$n.json"
done
run_watch 2
expect "Applies lead screen detection only to Claude Code" test "$(prompt_count)" = 1

# --- Lead status ---
new_case leadbusy
printf '%s\n' "$MARK" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 blocked "w1:p1|impl-9|w1:t1|blocked|6"
mklist 4 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 3
expect "Does not send while lead is working or blocked" test "$(prompt_count)" = 0
expect "Notifies the human once while lead delivery is deferred" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1
rm -f "$STUB/calls" "$STUB/notify.log"; rm -rf "$STATE/w1"
run_watch 4
expect "Sends one current-state notice when lead becomes idle" test "$(prompt_count)" = 1

new_case leadbusy2
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|working|7"
mklist 4 idle "w1:p1|impl-9|w1:t1|working|7"
run_watch 4
expect "Discards an old stop if the worker resumes while lead is busy" test "$(prompt_count)" = 0

new_case batch
mklist 1 idle "w1:p1|impl-1|w1:t1|working|5" "w1:p2|impl-2|w1:t2|working|5"
mklist 2 idle "w1:p1|impl-1|w1:t1|done|6" "w1:p2|impl-2|w1:t2|done|6"
run_watch 2
expect "Batches multiple stops in the same round" test "$(prompt_count)" = 1
expect "Includes both workers in the batch" bash -c '[[ "$1" == *impl-1* && "$1" == *impl-2* ]]' _ "$(prompts)"

new_case sendfail
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
touch "$STUB/prompt.fail"
run_watch 3
expect "Reevaluates and retries failed delivery next round" test "$(prompt_count)" = 2

# --- Worker selection ---
new_case targets
mklist 1 idle "w1:p1|-|w1:t1|working|5" "w2:w2:p9|other|w2:t9|working|5" "w1:p3|lead-9-g1|w1:t3|working|5"
mklist 2 idle "w1:p1|-|w1:t1|done|6" "w2:w2:p9|other|w2:t9|done|6" "w1:p3|lead-9-g1|w1:t3|done|6"
run_watch 2
expect "Monitors unnamed workers" bash -c '[[ "$1" == *unnamed* ]]' _ "$(prompts)"
expect "Excludes other workspaces and lead-* agents" bash -c '[[ "$1" == 1 && "$(grep -c "agent: " "$2/prompts.log")" == 1 ]]' _ "$(prompt_count)" "$STUB"

new_case unknown
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|unknown|"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 3
expect "Ignores unknown then detects done" test "$(prompt_count)" = 1

# --- Exit conditions and lead resolution ---
new_case wsgone
mklist 0 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents=[]' "$STUB/list.1.json" >"$STUB/list.last.json"
echo 2 >"$STUB/ws.gone.from"
run_watch 50
expect "Exits on workspace_not_found" bash -c 'grep -q "closed" "$1"' _ "$STATE/w1/watch.log"
expect "Removes the PID file on exit" test ! -e "$STATE/w1/watch.pid"

new_case grace
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents|=map(select(.name!="lead-9"))' "$STUB/list.1.json" >"$STUB/list.2.json"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
cp "$STUB/list.3.json" "$STUB/list.last.json"
run_watch 3
expect "Continues if the lead is missing for one round" test "$(prompt_count)" = 1
new_case gone
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents|=map(select(.name!="lead-9"))' "$STUB/list.1.json" >"$STUB/list.2.json"
cp "$STUB/list.2.json" "$STUB/list.last.json"
run_watch 50
expect "Exits if the lead is missing for two consecutive rounds" bash -c 'grep -q "disappeared" "$1"' _ "$STATE/w1/watch.log"

new_case nextlead
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents|=map(if .name=="lead-9" then .name="lead-9-next" else . end)' "$STUB/list.1.json" >"$STUB/list.last.json"
cp "$STUB/list.last.json" "$STUB/list.2.json"
run_watch 5
expect "Accepts lead-<N>-next for any number of rounds" bash -c '! grep -q "disappeared" "$1" && grep -q "max rounds" "$1"' _ "$STATE/w1/watch.log"

new_case otherws
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
cp "$STUB/list.1.json" "$STUB/list.0.json"
jq -c '.result.agents|=map(if .name=="lead-9" then .workspace_id="w2" else . end)' "$STUB/list.1.json" >"$STUB/list.last.json"
run_watch 50
expect "Does not confuse another workspace's same-named lead" bash -c 'grep -q "disappeared" "$1"' _ "$STATE/w1/watch.log"

new_case nexttarget
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
cp "$STUB/list.1.json" "$STUB/list.0.json"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
for n in 1 2; do jq -c '.result.agents|=map(if .name=="lead-9" then .name="lead-9-next" else . end)' "$STUB/list.$n.json" >"$STUB/list.$n.tmp" && mv "$STUB/list.$n.tmp" "$STUB/list.$n.json"; done
run_watch 2
expect "Sends to lead-<N>-next when the active lead is absent" test "$(head -1 "$STUB/prompt.targets" 2>/dev/null)" = lead-9-next
new_case nextprefer
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
for n in 1 2; do jq -c '.result.agents+=[{agent:"claude",agent_status:"idle",name:"lead-9-next",pane_id:"w1:p8",tab_id:"w1:t8",workspace_id:"w1"}]' "$STUB/list.$n.json" >"$STUB/list.$n.tmp" && mv "$STUB/list.$n.tmp" "$STUB/list.$n.json"; done
run_watch 2
expect "Prefers the active lead over lead-<N>-next" test "$(head -1 "$STUB/prompt.targets" 2>/dev/null)" = lead-9

new_case flaky
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
echo FAIL >"$STUB/list.2.json"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 3
expect "Continues after a failed connection round" test "$(prompt_count)" = 1

# --- Late workers, process exits, and launch regressions ---
new_case lateidle
printf 'どちらにしますか？\n' >"$STUB/read.w1_p2"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|working|5" "w1:p2|impl-8|w1:t2|idle|9"
run_watch 2
expect "Wakes idle workers first seen after initial observation" test "$(prompt_count)" = 1

new_case exited
printf 'プロセスが終わった後の画面\n' >"$STUB/read.w1_p1"
touch "$STUB/pane.alive.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle
mklist 3 idle
run_watch 3
expect "Notifies process exit when the agent disappears but its pane remains" bash -c '[[ "$1" == *"stopped (process exited)"* && "$1" == *impl-9* && "$1" == *プロセスが終わった後の画面* && "$2" = 1 ]]' _ "$(prompts)" "$(prompt_count)"
new_case tabclosed
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle
mklist 3 idle
run_watch 3
expect "Does not notify when the pane disappears with its tab" test "$(prompt_count)" = 0

new_case nextname
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_FOREGROUND=1 AGENT_GRAPH_WATCH_INTERVAL=0 AGENT_GRAPH_WATCH_MAX_ROUNDS=2 bash "$WATCH" lead-9-next 2>&1)"
expect "Normalizes the temporary -next launch name to the active lead" bash -c 'grep -q "lead=lead-9 " "$1" && ! grep -q "lead=lead-9-next" "$1"' _ "$STATE/w1/watch.log"

new_case footer
printf '  ⏵⏵ bypass permissions on · 1 shell · ← 5 agents\n  agent-a   running\n  agent-b   running\n  agent-c   running\n  agent-d   idle\n  agent-e   idle\n  agent-f   idle\n  agent-g   idle\n  agent-h   idle\n  agent-i   idle\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Finds a shell footer above a background agent list" test "$(prompt_count)" = 0
new_case footer2
printf '✻ Brewed · done · 1 shell still running\n  ⏵⏵ bypass permissions on · 1 agent\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Ignores shell text outside the footer" test "$(prompt_count)" = 1

new_case nosetsid
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents=[]' "$STUB/list.1.json" >"$STUB/list.last.json"
cp "$STUB/list.1.json" "$STUB/list.0.json"
out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_NO_SETSID=1 AGENT_GRAPH_WATCH_INTERVAL=0.1 bash "$WATCH" lead-9 2>&1)"; rc=$?
expect "Detaches with nohup alone without setsid" bash -c '[[ "$1" -eq 0 && "$2" == *started:* ]]' _ "$rc" "$out"

new_case quiet
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents=[]' "$STUB/list.1.json" >"$STUB/list.last.json"
cp "$STUB/list.1.json" "$STUB/list.0.json"
err="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_INTERVAL=0.1 bash "$WATCH" lead-9 2>&1 >/dev/null)"
expect "First launch without a PID file emits no stderr" test -z "$err"

new_case noseq
mklist 1 idle
jq -c '.result.agents|=map(del(.state_change_seq))' "$STUB/list.1.json" >"$STUB/list.tmp" && mv "$STUB/list.tmp" "$STUB/list.1.json"
out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_FOREGROUND=1 bash "$WATCH" lead-9 2>&1)"; rc=$?
expect "Fails clearly when herdr lacks state_change_seq" bash -c '[[ "$1" -ne 0 && "$2" == *state_change_seq* ]]' _ "$rc" "$out"

new_case hook
printf '  ⏵⏵ running PostToolBatch hook\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 3
expect "Persistent unmarked blocked wakes even with hook text" test "$(prompt_count)" = 1
new_case hookmark
printf '  running PostToolBatch hook\n Do you want to proceed?\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 2
expect "Confirmation markers wake even with hook text" test "$(prompt_count)" = 1

new_case notifyfail
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 4 idle "w1:p1|impl-9|w1:t1|done|6"
mklist 5 idle "w1:p1|impl-9|w1:t1|done|6"
touch "$STUB/notify.fail"
run_watch 5
expect "Retries failed human notices while stopped after lead delivery" test "$(grep -c 'notification show' "$STUB/notify.log")" = 3
rm -f "$STUB/notify.fail" "$STUB/calls" "$STUB/notify.log" "$STUB/prompts.log"; rm -rf "$STATE/w1"
run_watch 5
expect "Stops human retries after success independently of lead delivery" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1

# Notify the human after lead delivery or two observations of the same stop.
new_case humanquiet
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|done|6"
mklist 3 idle "w1:p1|impl-9|w1:t1|working|7"
mklist 4 idle "w1:p1|impl-9|w1:t1|working|7"
run_watch 4
expect "Does not notify a one-round stop while lead is busy" test ! -s "$STUB/notify.log"
new_case humantwice
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|done|6"
mklist 3 working "w1:p1|impl-9|w1:t1|done|6"
run_watch 3
expect "Notifies a persistent stop after two rounds even if lead is busy" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1
expect "Still defers delivery to the lead" test "$(prompt_count)" = 0
new_case humansent
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "Notifies humans in the round delivered to the lead" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1

# Marked blocked notifies the human immediately even while the lead is busy.
new_case urgent
printf '%s\n' "$MARK" >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 2
expect "Notifies marked blocked immediately even if lead is working" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1
expect "Still defers delivery to the lead" test "$(prompt_count)" = 0
new_case urgent2
printf '1. A\n' >"$STUB/read.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 working "w1:p1|impl-9|w1:t1|blocked|6"
mklist 3 working "w1:p1|impl-9|w1:t1|blocked|6"
mklist 4 working "w1:p1|impl-9|w1:t1|blocked|6"
run_watch 3
expect "Does not notify humans on the first eligible unmarked blocked round while lead is busy" test ! -s "$STUB/notify.log"
rm -f "$STUB/calls"; rm -rf "$STATE/w1"
run_watch 4
expect "Notifies unmarked blocked after two eligible stop observations" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1

# Abandon human retries after three failures for the same stop.
new_case giveup
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
for n in 2 3 4 5 6 7 8; do mklist $n idle "w1:p1|impl-9|w1:t1|done|6"; done
touch "$STUB/notify.fail"
run_watch 8
expect "Stops human retries after three failures" test "$(grep -c 'notification show' "$STUB/notify.log")" = 3
expect "Logs giving up exactly once" test "$(grep -c 'failed 3 times; giving up' "$STATE/w1/watch.log")" = 1
expect "Lead delivery succeeds even when human delivery is abandoned" test "$(prompt_count)" = 1

# Initial observation means the first actual call to observe.
new_case firstfail
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
cp "$STUB/list.1.json" "$STUB/list.0.json"
echo FAIL >"$STUB/list.1.json"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "First successful observation records done only after an initial list failure" test "$(prompt_count)" = 0
new_case firstnolead
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
cp "$STUB/list.1.json" "$STUB/list.0.json"
jq -c '.result.agents|=map(select(.name!="lead-9"))' "$STUB/list.1.json" >"$STUB/list.1.tmp" && mv "$STUB/list.1.tmp" "$STUB/list.1.json"
mklist 2 idle "w1:p1|impl-9|w1:t1|done|6"
run_watch 2
expect "First successful observation records done only after an initially missing lead" test "$(prompt_count)" = 0

# Process disappeared panes once; keep EXITED separate from NOTIFIED.
new_case exited2
touch "$STUB/pane.alive.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
mklist 2 idle
mklist 3 idle
run_watch 3
expect "Processes each disappeared pane once per round" bash -c '[[ "$(grep -c "agent: " "$1/prompts.log")" == 1 ]]' _ "$STUB"
new_case exited3
touch "$STUB/pane.alive.w1_p1"
mklist 1 idle "w1:p1|impl-9|w1:t1|done|5"
mklist 2 idle
mklist 3 idle "w1:p1|impl-9|w1:t1|done|5"
mklist 4 idle "w1:p1|impl-9|w1:t1|done|5"
run_watch 4
expect "Does not repeat a stop when an exited agent returns with the same seq" test "$(prompt_count)" = 1
expect "Does not repeat human stop notices after an exit with the same seq" test "$(grep -c 'notification show' "$STUB/notify.log")" = 1

# --- Arguments ---
new_case args
expect "Fails without arguments" bash -c '! STUB_DIR="$1" PATH="$2:$PATH" bash "$3" >/dev/null 2>&1' _ "$STUB" "$BIN" "$WATCH"
mklist 1 idle
expect "Fails when the lead does not exist" bash -c '! STUB_DIR="$1" PATH="$2:$PATH" AGENT_GRAPH_STATE_ROOT="$3" bash "$4" lead-77 >/dev/null 2>&1' _ "$STUB" "$BIN" "$STATE" "$WATCH"

# --- Launch exclusion with PID/starttime and short-lived locks ---
proc_start() { sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
# $1=lead (default lead-9); run a single round.
start_fg() {
  rm -f "$STUB/calls"
  STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_FOREGROUND=1 AGENT_GRAPH_WATCH_INTERVAL=0 \
    AGENT_GRAPH_WATCH_MAX_ROUNDS=1 timeout 20 bash "$WATCH" "${1:-lead-9}" >/dev/null 2>&1
}
if [[ -r /proc/self/stat ]]; then
  new_case dup
  mklist 1 idle
  mkdir -p "$STATE/w1"
  sleep 30 & holder=$!
  echo "$holder $(proc_start "$holder") lead-9" >"$STATE/w1/watch.pid"
  out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" bash "$WATCH" lead-9 2>&1)"; rc=$?
  expect "Launcher exits zero without action for a live same-lead watcher" bash -c '[[ "$1" -eq 0 && "$2" == *"already running:"* ]]' _ "$rc" "$out"
  rm -f "$STUB/calls"
  out2="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_FOREGROUND=1 bash "$WATCH" lead-9 2>&1)"; rc2=$?
  expect "Foreground watcher exits zero without action for a live same-lead watcher" bash -c '[[ "$1" -eq 0 && "$2" == *"already running:"* ]]' _ "$rc2" "$out2"

  # A different starttime identifies PID reuse, not a live watcher.
  echo "$holder 1 lead-9" >"$STATE/w1/watch.pid"; : >"$STATE/w1/watch.log"
  mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
  start_fg
  expect "Starts a watcher when starttime shows PID reuse" bash -c 'grep -q "start workspace" "$1"' _ "$STATE/w1/watch.log"

  # A dead PID record must not prevent startup.
  kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
  echo "$holder 12345 lead-9" >"$STATE/w1/watch.pid"; : >"$STATE/w1/watch.log"
  start_fg
  expect "Starts despite a dead PID record" bash -c 'grep -q "start workspace" "$1"' _ "$STATE/w1/watch.log"

  # A child holding the lock file open must not prevent another launch.
  : >"$STATE/w1/watch.log"; rm -f "$STATE/w1/watch.pid"
  ( exec sleep 30 9>"$STATE/w1/watch.lock" ) & leftover=$!
  sleep 0.2
  start_fg
  expect "Starts despite a child holding the lock file open" bash -c 'grep -q "start workspace" "$1"' _ "$STATE/w1/watch.log"
  kill "$leftover" 2>/dev/null; wait "$leftover" 2>/dev/null

  # Replace a watcher for another lead with one for the requested lead.
  new_case relead
  mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
  jq -c '.result.agents+=[{agent:"claude",agent_status:"idle",name:"lead-7",pane_id:"w1:p7",tab_id:"w1:t7",workspace_id:"w1",state_change_seq:2}]' "$STUB/list.1.json" >"$STUB/list.tmp" && mv "$STUB/list.tmp" "$STUB/list.1.json"
  cp "$STUB/list.1.json" "$STUB/list.last.json"
  out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_INTERVAL=1 bash "$WATCH" lead-9 2>&1)"
  oldpid="$(cut -d' ' -f1 "$STATE/w1/watch.pid")"
  out2="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_INTERVAL=1 bash "$WATCH" lead-7 2>&1)"
  newpid="$(cut -d' ' -f1 "$STATE/w1/watch.pid")"; newlead="$(cut -d' ' -f3 "$STATE/w1/watch.pid")"
  sleep 0.3
  expect "Explains stopping and restarting for a different lead" bash -c '[[ "$1" == *"lead-9"*"stopping"*"lead-7"* && "$2" == *started:* ]]' _ "$out2" "$out2"
  expect "Replaces the PID file with the new watcher and lead" bash -c '[[ "$1" != "$2" && "$3" == lead-7 ]] && ! kill -0 "$2" 2>/dev/null && kill -0 "$1" 2>/dev/null' _ "$newpid" "$oldpid" "$newlead"
  kill "$newpid" 2>/dev/null

  # Four concurrent launches leave one watcher with either flock or mkdir locks.
  for mode in flock mkdir; do
    new_case "race-$mode"
    mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
    cp "$STUB/list.1.json" "$STUB/list.last.json"
    nf=""; [[ "$mode" == mkdir ]] && nf=1
    for _ in 1 2 3 4; do
      STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_INTERVAL=1 AGENT_GRAPH_NO_FLOCK="$nf" bash "$WATCH" lead-9 >"$STATE/launch.$_" 2>&1 &
    done
    wait
    sleep 0.5
    n="$(grep -c 'start workspace' "$STATE/w1/watch.log" 2>/dev/null)"
    pid="$(cut -d' ' -f1 "$STATE/w1/watch.pid" 2>/dev/null)"
    started="$(cat "$STATE"/launch.* | grep -c 'started:')"
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
    expect "Concurrent launches leave one watcher ($mode)" test "$n" = 1
    expect "Only one concurrent launcher prints started ($mode)" test "$started" = 1
  done
else
  printf 'ok launch exclusion # SKIP /proc unavailable\n'
fi

# --- Detached startup and automatic exit ---
new_case bg
mklist 1 idle "w1:p1|impl-9|w1:t1|working|5"
jq -c '.result.agents=[]' "$STUB/list.1.json" >"$STUB/list.last.json"
cp "$STUB/list.1.json" "$STUB/list.0.json"
out="$(STUB_DIR="$STUB" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$STATE" AGENT_GRAPH_WATCH_INTERVAL=0.1 bash "$WATCH" lead-9 2>&1)"; rc=$?
expect "Returns after confirming the detached child recorded its PID" bash -c '[[ "$1" -eq 0 && "$2" == *started:* ]]' _ "$rc" "$out"
for _ in $(seq 1 50); do grep -q "disappeared" "$STATE/w1/watch.log" 2>/dev/null && break; sleep 0.1; done
expect "Detached watcher exits when its lead disappears" bash -c 'grep -q "disappeared" "$1"' _ "$STATE/w1/watch.log"

printf '\n# agent-graph-watch: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
