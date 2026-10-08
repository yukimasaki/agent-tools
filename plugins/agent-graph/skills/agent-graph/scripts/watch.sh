#!/usr/bin/env bash
# agent-graph watch — monitor workers in one herdr workspace and wake the lead.
# Usage: watch.sh <lead-agent-name>
# One watcher per workspace. An existing watcher for another lead is replaced.
# Decisions use current state each round; records track delivery and observations,
# not a persistent graph. Equal state_change_seq values identify the same stop.
# done -> idle does not change that sequence. Never compare sequence increments.
# Human notices: immediately for blocked with confirmation markers; otherwise
# after lead delivery or two observations. Retry failures up to three times.
# Lead delivery waits during working/blocked or a Claude Code question screen.
# Pi lead question screens are not detected. Re-read Claude Code just before send.
# Environment:
#   AGENT_GRAPH_STATE_ROOT        PID/log root (default ~/.local/state/agent-graph)
#   AGENT_GRAPH_WATCH_INTERVAL    Poll interval in seconds (default 30)
#   AGENT_GRAPH_WATCH_FOREGROUND  1 disables detachment (tests)
#   AGENT_GRAPH_WATCH_MAX_ROUNDS  Stop after this many rounds; 0 is unlimited
#   AGENT_GRAPH_NO_SETSID         1 uses nohup alone (tests)
#   AGENT_GRAPH_NO_FLOCK          1 uses mkdir locks instead of flock (tests)

if (( BASH_VERSINFO[0] < 4 )); then
  echo "agent-graph requires Bash 4 or newer (current: $BASH_VERSION)" >&2
  exit 1
fi
set -uo pipefail

STATE_ROOT="${AGENT_GRAPH_STATE_ROOT:-$HOME/.local/state/agent-graph}"
INTERVAL="${AGENT_GRAPH_WATCH_INTERVAL:-30}"
MAX_ROUNDS="${AGENT_GRAPH_WATCH_MAX_ROUNDS:-0}"
# Last pane lines included in the notice.
TAIL_LINES=8
# Search beyond the notice tail: background agent lists can follow the footer.
FOOTER_LINES=20
# Match shells only in a footer beginning with ⏵⏵. Historical completion lines
# can retain "still running" after a shell exits. No matching footer means wake.
SHELL_RE='· [0-9]+ shells?( ·|[[:space:]]*$)'
FOOTER_RE='^[[:space:]]*⏵⏵'
PERMISSION_RE='Do you want to proceed\?|Permission rule'
# Inspect the last nonblank lines, excluding copied screens higher in the chat.
LEAD_PROMPT_FOOTER_LINES=3
# Claude Code only: Enter instructions must include Esc on the same line.
# Permission screens are detected from a footer beginning with Esc to cancel.
LEAD_PROMPT_RE='^[[:space:]]*(Enter to (select|confirm)\b.*Esc to cancel|Esc to cancel\b)'
# Worker blocked markers: permission prose or numbered selection options.
PROMPT_MARK_RE="$PERMISSION_RE|❯ [0-9]\\."

die() { echo "agent-graph watch: $*" >&2; exit 1; }

case "${1:-}" in
  -h|--help) echo "Usage: watch.sh <lead-agent-name>"; exit 0 ;;
  "") die "a lead agent name is required (example: watch.sh lead-123)" ;;
  -*) die "unknown option: $1" ;;
esac
# Normalize the temporary handoff name to the active lead name.
LEAD="${1%-next}"
[[ "$LEAD" =~ ^lead-[a-z0-9_-]+$ ]] || die "lead name must use lead-<number-or-name>: $LEAD"
command -v herdr >/dev/null 2>&1 || die "herdr not found"
command -v jq >/dev/null 2>&1 || die "jq not found"


LIST_JSON="$(herdr agent list 2>/dev/null)" || die "herdr agent list failed"
# Prefer HERDR_WORKSPACE_ID when the name appears in multiple workspaces.
WS="$(jq -r --arg n "$LEAD" --arg pref "${HERDR_WORKSPACE_ID:-}" '
  [.result.agents[]? | select(.name == $n)] as $m
  | ([$m[] | select(.workspace_id == $pref)][0] // $m[0]).workspace_id // empty' <<<"$LIST_JSON" 2>/dev/null)"
[[ -n "$WS" ]] || die "agent '$LEAD' not found; name it with herdr agent rename first"
jq -e --arg n "$LEAD" --arg ws "$WS" '[.result.agents[]? | select(.name == $n and .workspace_id == $ws)][0] | has("state_change_seq")' >/dev/null 2>&1 <<<"$LIST_JSON" \
  || die "herdr agent list has no state_change_seq; this watcher requires that field"

STATE_DIR="$STATE_ROOT/$WS"
PID_FILE="$STATE_DIR/watch.pid"
LOG_FILE="$STATE_DIR/watch.log"
mkdir -p "$STATE_DIR" || die "cannot create $STATE_DIR"
HAVE_FLOCK=false
if [[ "${AGENT_GRAPH_NO_FLOCK:-0}" != 1 ]] && command -v flock >/dev/null 2>&1; then HAVE_FLOCK=true; fi

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >>"$LOG_FILE"; }


# $1=lock name; $2=fd. Hold briefly. Without flock, acquire a mkdir lock;
# reclaim a directory older than 30 seconds. Do not hold locks during monitoring.
lock_take() {
  local path="$STATE_DIR/$1" fd="$2" age
  if $HAVE_FLOCK; then
    eval "exec $fd>\"\$path.lock\""
    flock "$fd"
  else
    until mkdir "$path.d" 2>/dev/null; do
      age=$(( $(date +%s) - $(stat -c %Y "$path.d" 2>/dev/null || stat -f %m "$path.d" 2>/dev/null || date +%s) ))
      (( age > 30 )) && rmdir "$path.d" 2>/dev/null
      sleep 0.05
    done
  fi
}
lock_drop() {
  local path="$STATE_DIR/$1" fd="$2"
  if $HAVE_FLOCK; then eval "exec $fd>&-"; else rmdir "$path.d" 2>/dev/null; fi
}


# Linux /proc starttime or ps lstart prevents confusing a reused PID.
proc_start() {
  local pid="$1"
  if [[ -r "/proc/$pid/stat" ]]; then
    sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}'
  else
    ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//; s/ /_/g'
  fi
}

# PID file: <pid> <starttime> <lead>. Return <pid> <lead> only if still alive.
running_pid() {
  local pid start lead
  read -r pid start lead 2>/dev/null <"$PID_FILE" || return 1
  [[ "$pid" =~ ^[0-9]+$ && -n "$start" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ "$(proc_start "$pid" | sed 's/ /_/g')" == "$start" ]] || return 1
  echo "$pid ${lead:-}"
}

stop_pid() {
  local pid="$1" _
  kill "$pid" 2>/dev/null || return 0
  for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.1; done
  kill -9 "$pid" 2>/dev/null || true
}

# Write our identity under a short lock. Return 3 for an existing same-lead
# watcher; stop a different-lead watcher before claiming this workspace.
claim() {
  local cur cur_pid cur_lead
  lock_take watch 9
  if cur="$(running_pid)"; then
    read -r cur_pid cur_lead <<<"$cur"
    if [[ "$cur_pid" != "$$" ]]; then
      if [[ "$cur_lead" == "$LEAD" ]]; then lock_drop watch 9; return 3; fi
      stop_pid "$cur_pid"
      log "stopped watcher $cur_pid for a different lead (${cur_lead:-unknown})"
    fi
  fi
  echo "$$ $(proc_start "$$" | sed 's/ /_/g') $LEAD" >"$PID_FILE"
  lock_drop watch 9
}


if [[ "${AGENT_GRAPH_WATCH_FOREGROUND:-0}" != 1 ]]; then
  # Serialize launchers; close this fd in the detached child.
  lock_take start 8
  if cur="$(running_pid)"; then
    read -r cur_pid cur_lead <<<"$cur"
    if [[ "$cur_lead" == "$LEAD" ]]; then
      lock_drop start 8
      echo "already running: workspace=$WS pid=$cur_pid lead=$cur_lead log=$LOG_FILE"
      exit 0
    fi
    echo "watcher for a different lead (${cur_lead:-unknown}): stopping $cur_pid and restarting for $LEAD"
  fi
  # Use nohup alone on systems without setsid (such as macOS).
  detach=(nohup)
  if [[ "${AGENT_GRAPH_NO_SETSID:-0}" != 1 ]] && command -v setsid >/dev/null 2>&1; then detach=(setsid nohup); fi
  AGENT_GRAPH_WATCH_FOREGROUND=1 "${detach[@]}" bash "${BASH_SOURCE[0]}" "$LEAD" >>"$LOG_FILE" 2>&1 </dev/null 8>&- &
  # Return only after the child records its PID and is confirmed alive.
  p=""
  for _ in $(seq 1 50); do
    if cur="$(running_pid)"; then
      read -r cur_pid cur_lead <<<"$cur"
      [[ "$cur_lead" == "$LEAD" ]] && { p="$cur_pid"; break; }
    fi
    sleep 0.1
  done
  lock_drop start 8
  if [[ -n "$p" ]]; then
    echo "started: workspace=$WS pid=$p lead=$LEAD log=$LOG_FILE"
    exit 0
  fi
  die "cannot confirm watcher startup; check the log: $LOG_FILE"
fi


claim
rc=$?
if (( rc == 3 )); then
  echo "already running: workspace=$WS"
  exit 0
fi
(( rc == 0 )) || die "cannot write PID file: $PID_FILE"
trap '[[ "$(cut -d" " -f1 "$PID_FILE" 2>/dev/null)" == "$$" ]] && rm -f "$PID_FILE"' EXIT

# Per-pane records: lead/human notified seq, previous blocked seq, exited
# delivery, previous wake seq, human failure key/count, tail cache, name/tab.
declare -A NOTIFIED=() HUMAN=() BLOCKED_SEEN=() EXITED=() EXITED_HUMAN=() WAKE_SEEN=() HUMAN_FAIL=() TAIL_CACHE=() LAST_NAME=() LAST_TAB=()
LEAD_TARGET=""; LEAD_STATUS=""; LEAD_KIND=""
# Current-round stops. URGENT: confirmation markers; TOLD: lead already told;
# SENT: delivered this round. OBSERVED means observe has run at least once.
W_PANE=(); W_NAME=(); W_TAB=(); W_STATUS=(); W_SEQ=(); W_TOLD=(); W_SENT=(); W_URGENT=()
OBSERVED=false
lead_missing=0
round=0

log "start workspace=$WS lead=$LEAD interval=${INTERVAL}s"

agent_name_of() { [[ "$1" == "-" ]] && echo "unnamed" || echo "$1"; }

# Cache pane tails within a round. $2=fresh discards the cache before reading.
# Return the read status. Call directly: command substitution loses cache writes.
load_tail() {
  [[ "${2:-}" != fresh ]] || unset "TAIL_CACHE[$1]"
  [[ -z "${TAIL_CACHE[$1]+x}" ]] || return 0
  TAIL_CACHE[$1]="$(herdr agent read "$1" --lines "$FOOTER_LINES" 2>/dev/null)"
}
# Call after load_tail; $2=line count (default TAIL_LINES).
tail_of() { tail -n "${2:-$TAIL_LINES}" <<<"${TAIL_CACHE[$1]}"; }
# Match the shell count in the current footer, not historical completion prose.
has_bg_shell() { grep -E "$FOOTER_RE" <<<"${TAIL_CACHE[$1]}" | grep -Eq "$SHELL_RE"; }

# Queue: pane, name, tab, status, seq ("exited" for an exit), told, urgent.
push_stop() {
  W_PANE+=("$1"); W_NAME+=("$2"); W_TAB+=("$3"); W_STATUS+=("$4"); W_SEQ+=("$5"); W_TOLD+=("${6:-0}"); W_SENT+=(0); W_URGENT+=("${7:-0}")
}

# Decide from current state. Retry human notices for already delivered stops
# until human delivery succeeds or is abandoned. Args: pane/name/tab/status/seq.
decide() {
  local pane="$1" name="$2" tab="$3" status="$4" seq="$5" wake=false urgent=0
  case "$status" in
    working)
      unset "BLOCKED_SEEN[$pane]"
      return ;;
    blocked)
      if [[ "${NOTIFIED[$pane]-}" == "$seq" ]]; then
        [[ "${HUMAN[$pane]-}" == "$seq" ]] || push_stop "$pane" "$name" "$tab" "$status" "$seq" 1
        return
      fi
      load_tail "$pane"
      if tail_of "$pane" | grep -Eq "$PROMPT_MARK_RE"; then
        wake=true; urgent=1
      elif [[ "${BLOCKED_SEEN[$pane]-}" == "$seq" ]]; then
        wake=true   # Same blocked sequence in two consecutive observations.
      else
        BLOCKED_SEEN[$pane]="$seq"
        log "$(agent_name_of "$name") ($pane): blocked without confirmation markers; wait until the next round"
      fi ;;
    done|idle)
      if [[ "${NOTIFIED[$pane]-}" == "$seq" ]]; then
        [[ "${HUMAN[$pane]-}" == "$seq" ]] || push_stop "$pane" "$name" "$tab" "$status" "$seq" 1
        return
      fi
      load_tail "$pane"
      if has_bg_shell "$pane"; then
        # Keep records unchanged; wake on the same seq once the shell disappears.
        return
      fi
      wake=true ;;
    *) return ;;
  esac
  $wake || return
  push_stop "$pane" "$name" "$tab" "$status" "$seq" 0 "$urgent"
}

# First successful observation only records workers already done/idle. Workers
# first seen later use normal decisions, including newly launched idle workers.
observe() {
  local list_json="$1" pane name tab status seq key
  local seen=" " first=true
  $OBSERVED && first=false
  OBSERVED=true
  W_PANE=(); W_NAME=(); W_TAB=(); W_STATUS=(); W_SEQ=(); W_TOLD=(); W_SENT=(); W_URGENT=()
  local -A keys=()
  while IFS=$'\t' read -r pane name tab status seq; do
    [[ -n "$pane" ]] || continue
    seen+="$pane "
    [[ "$status" == unknown ]] && continue
    LAST_NAME[$pane]="$name"; LAST_TAB[$pane]="$tab"
    # Returning agents keep human seq, avoiding duplicate exit/stop notices.
    unset "EXITED[$pane]" "EXITED_HUMAN[$pane]"
    if [[ -z "${NOTIFIED[$pane]+x}" ]]; then
      if $first && [[ "$status" == done || "$status" == idle ]]; then
        NOTIFIED[$pane]="$seq"; HUMAN[$pane]="$seq"
        log "$(agent_name_of "$name") ($pane): first observation $status (record only)"
        continue
      fi
      NOTIFIED[$pane]=""
    fi
    decide "$pane" "$name" "$tab" "$status" "$seq"
  done < <(jq -r --arg ws "$WS" '.result.agents[]?
      | select(.workspace_id == $ws and ((.name // "") | startswith("lead-") | not))
      | [.pane_id, (.name // "-"), .tab_id, .agent_status, ((.state_change_seq // "-") | tostring)] | @tsv' <<<"$list_json")
  # Missing agents with surviving panes are exited. Missing panes lose records.
  # EXITED is separate from NOTIFIED so the old sequence is preserved.
  for key in "${!NOTIFIED[@]}" "${!HUMAN[@]}" "${!BLOCKED_SEEN[@]}" "${!EXITED[@]}"; do keys[$key]=1; done
  for pane in "${!keys[@]}"; do
    [[ "$seen" != *" $pane "* ]] || continue
    if herdr pane get "$pane" >/dev/null 2>&1; then
      if [[ -z "${EXITED[$pane]:-}" ]]; then
        push_stop "$pane" "${LAST_NAME[$pane]:--}" "${LAST_TAB[$pane]:-}" exited exited 0
        log "${LAST_NAME[$pane]:-$pane} ($pane): removed from agent list but pane remains (process exited)"
      elif [[ -z "${EXITED_HUMAN[$pane]:-}" ]]; then
        push_stop "$pane" "${LAST_NAME[$pane]:--}" "${LAST_TAB[$pane]:-}" exited exited 1
      fi
    else
      unset "NOTIFIED[$pane]" "HUMAN[$pane]" "BLOCKED_SEEN[$pane]" "EXITED[$pane]" "EXITED_HUMAN[$pane]" "WAKE_SEEN[$pane]" "HUMAN_FAIL[$pane]" "LAST_NAME[$pane]" "LAST_TAB[$pane]" "TAIL_CACHE[$pane]"
      log "$pane: pane disappeared; discarded records"
    fi
  done
}

# Human delivery is independent of lead delivery. Retry while stopped; after
# three failures mark as notified to avoid indefinite retries for the same stop.
notify_human() {
  local i sound what pane key fails
  local -A next_seen=()
  for i in "${!W_PANE[@]}"; do
    pane="${W_PANE[$i]}"; key="${W_SEQ[$i]}"
    if [[ "${W_TOLD[$i]}" != 1 && "${W_SENT[$i]}" != 1 && "${W_URGENT[$i]}" != 1 ]]; then
      next_seen[$pane]="$key"
      [[ "${WAKE_SEEN[$pane]-}" == "$key" ]] || continue
    fi
    if [[ "${W_STATUS[$i]}" == exited ]]; then
      [[ -n "${EXITED_HUMAN[$pane]:-}" ]] && continue
    else
      [[ "${HUMAN[$pane]-}" == "$key" ]] && continue
    fi
    sound=done; [[ "${W_STATUS[$i]}" == blocked ]] && sound=request
    what="${W_STATUS[$i]}"; [[ "$what" == exited ]] && what="exited"
    if ! herdr notification show "$LEAD: $(agent_name_of "${W_NAME[$i]}") is $what" --sound "$sound" >/dev/null 2>&1; then
      fails=0
      [[ "${HUMAN_FAIL[$pane]-}" == "$key "* ]] && fails="${HUMAN_FAIL[$pane]#* }"
      fails=$((fails + 1))
      HUMAN_FAIL[$pane]="$key $fails"
      if (( fails >= 3 )); then
        if [[ "${W_STATUS[$i]}" == exited ]]; then EXITED_HUMAN[$pane]=1; else HUMAN[$pane]="$key"; fi
        log "$(agent_name_of "${W_NAME[$i]}") ($pane): human notification failed 3 times; giving up"
      else
        log "$(agent_name_of "${W_NAME[$i]}") ($pane): human notification failed (attempt $fails); retry next round"
      fi
      continue
    fi
    unset "HUMAN_FAIL[$pane]"
    if [[ "${W_STATUS[$i]}" == exited ]]; then EXITED_HUMAN[$pane]=1; else HUMAN[$pane]="$key"; fi
    log "$(agent_name_of "${W_NAME[$i]}") ($pane): notified human of ${W_STATUS[$i]}"
  done
  WAKE_SEEN=()
  for pane in "${!next_seen[@]}"; do WAKE_SEEN[$pane]="${next_seen[$pane]}"; done
}

# Compose one notice, classifying from the current screen. $1=W_* index.
compose() {
  local i="$1" pane="${W_PANE[$1]}" cls label
  if [[ "${W_STATUS[$i]}" == exited ]]; then
    cls="stopped (process exited)"
    TAIL_CACHE[$pane]="$(herdr pane read "$pane" --lines "$FOOTER_LINES" 2>/dev/null || true)"
  elif [[ "${W_STATUS[$i]}" == blocked ]]; then
    if tail_of "$pane" | grep -Eq "$PERMISSION_RE"; then cls="permission request"; else cls="awaiting confirmation"; fi
  else
    cls="stopped"
  fi
  label="$(herdr tab get "${W_TAB[$i]}" 2>/dev/null | jq -r '.result.tab.label // empty' 2>/dev/null || true)"
  printf '[watch] %s entered %s (%s). Read the report file if present; otherwise inspect the pane and respond.\n' "$(agent_name_of "${W_NAME[$i]}")" "${W_STATUS[$i]}" "$cls"
  printf 'agent: %s / pane: %s / tab: %s / status: %s / classification: %s\n' "$(agent_name_of "${W_NAME[$i]}")" "$pane" "${label:-unknown}" "${W_STATUS[$i]}" "$cls"
  printf -- '--- last %s pane lines ---\n%s\n' "$TAIL_LINES" "$(tail_of "$pane")"
}

# Batch undelivered stops. Busy/question/read-failure deferrals do not modify
# delivery records. Re-read the lead by name after composing the batch.
flush() {
  local i body="" lead_tail
  local -a idx=()
  for i in "${!W_PANE[@]}"; do [[ "${W_TOLD[$i]}" == 1 ]] || idx+=("$i"); done
  ((${#idx[@]} > 0)) || return 0
  if [[ "$LEAD_STATUS" == working || "$LEAD_STATUS" == blocked ]]; then
    log "lead is $LEAD_STATUS; defer ${#idx[@]} notices (reevaluate next round)"
    return 0
  fi
  for i in "${idx[@]}"; do
    [[ -z "$body" ]] || body+=$'\n\n━━━━━━━━\n\n'
    body+="$(compose "$i")"
  done
  if [[ "$LEAD_KIND" == claude ]]; then
    if ! load_tail "$LEAD_TARGET" fresh; then
      log "cannot read lead screen; defer ${#idx[@]} notices (reevaluate next round)"
      return 0
    fi
    lead_tail="$(tail_of "$LEAD_TARGET" "$FOOTER_LINES" | sed '/^[[:space:]]*$/d' | tail -n "$LEAD_PROMPT_FOOTER_LINES")"
    if grep -Eq "$LEAD_PROMPT_RE" <<<"$lead_tail"; then
      log "lead displays a question screen; defer ${#idx[@]} notices (reevaluate next round)"
      return 0
    fi
  fi
  if herdr agent prompt "$LEAD_TARGET" "$body" >/dev/null 2>&1; then
    for i in "${idx[@]}"; do
      if [[ "${W_STATUS[$i]}" == exited ]]; then EXITED[${W_PANE[$i]}]=1; else NOTIFIED[${W_PANE[$i]}]="${W_SEQ[$i]}"; fi
      W_SENT[$i]=1
      unset "BLOCKED_SEEN[${W_PANE[$i]}]"
      log "woke lead ($LEAD_TARGET): $(agent_name_of "${W_NAME[$i]}") (${W_PANE[$i]}) ${W_STATUS[$i]}"
    done
  else
    log "cannot deliver to lead; reevaluate next round"
  fi
}

while true; do
  round=$((round + 1))
  TAIL_CACHE=()
  if list_json="$(herdr agent list 2>/dev/null)" && jq -e '.result.agents' >/dev/null 2>&1 <<<"$list_json"; then
    LEAD_TARGET=""; LEAD_STATUS=""; LEAD_KIND=""
    # Resolve within this workspace; prefer active lead over temporary -next.
    # jq replaces empty fields because shell IFS collapses adjacent tabs.
    IFS=$'\t' read -r LEAD_TARGET LEAD_STATUS LEAD_KIND < <(jq -r --arg ws "$WS" --arg n "$LEAD" '
      [.result.agents[]? | select(.workspace_id == $ws)] as $a
      | ([$a[] | select(.name == $n)][0] // [$a[] | select(.name == ($n + "-next"))][0] // empty)
      | [.name, .agent_status, .agent]
      | map(if . == null or . == "" then "-" else . end) | @tsv' <<<"$list_json")
    [[ "$LEAD_STATUS" != "-" ]] || LEAD_STATUS=""
    if [[ -z "$LEAD_TARGET" ]]; then
      ws_out="$(herdr workspace get "$WS" 2>&1 || true)"
      lead_missing=$((lead_missing + 1))
      if [[ "$ws_out" == *workspace_not_found* ]]; then
        log "workspace $WS closed; exiting"
        exit 0
      fi
      # Allow a one-round name gap during handoff.
      if (( lead_missing >= 2 )); then
        log "$LEAD disappeared; exiting"
        exit 0
      fi
      log "$LEAD not found (round ${lead_missing})"
    else
      lead_missing=0
      observe "$list_json"
      flush
      notify_human
    fi
  else
    log "cannot connect to herdr; retry next round"
  fi
  if (( MAX_ROUNDS > 0 && round >= MAX_ROUNDS )); then
    log "max rounds ($MAX_ROUNDS) reached; exiting"
    exit 0
  fi
  sleep "$INTERVAL"
done
