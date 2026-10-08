# Operational pitfalls

## A worker enters done with a background shell still running

Claude Code can end its response in `done` while background work continues. The screen may show `· done <time> · 1 shell still running` and a footer containing `· 1 shell`. The watcher treats the footer's `· N shell` as waiting rather than a stop requiring attention. It uses the current screen each round, so removing the footer triggers a notice for the same stop. It ignores `still running` in a completion line because that historical line can remain after the shell exits.

**Workers must stop background servers and shells before ending their turn**, or the watcher may continue treating them as waiting. See [messaging.md](messaging.md).

## Permission prompts despite --dangerously-skip-permissions

Commands can still stop with `Permission rule Bash(...) requires confirmation` in bypass mode. Settings permission rules appear to take precedence over bypass mode.

- `rm -rf *` when the current directory cannot be determined statically. Use an absolute path to a directory created with `mktemp -d`.
- `gh pr merge`.
- `git push --force-with-lease`.

Merges and force pushes depend on human authorization. Do not approve automatically. The watcher detects `blocked`; the lead reads the action before answering. See [herdr.md](herdr.md).

## A done → blocked transition hides intervening work

A worker can briefly resume `working` after a CI notice and then request confirmation. Polling every 30 seconds may observe only `done -> blocked`. Checking only whether the previous observation was `working` misses this. The watcher detects `blocked` regardless of the previous status.

## Transient blocked without a confirmation screen

Hooks can briefly show `blocked` without a confirmation screen. If the last eight pane lines contain `Do you want to proceed?`, `Permission rule`, or a `❯ 1.` option, the watcher reacts immediately. Without these markers, it waits for a second consecutive observation of the same `blocked` stop.

Sending a prompt during this period may fail with `agent_blocked`; see [messaging.md](messaging.md).

## agent_name_taken

Find the pane that holds the name and clear its agent name:

```bash
herdr agent list | jq -r '.result.agents[] | "\(.pane_id)\t\(.name // "-")"'
herdr agent rename <pane_id> --clear
```

`herdr pane rename --clear` clears only the pane label. **Agent names are separate.**

## Codex context remains at the default limit

`-c model_context_window` reaches the UI but not the inference app-server. Set the value in the top-level block of `~/.codex/config.toml` instead:

```toml
model_context_window = 872000
```

This is an example value, not a universal limit. Look up the selected model with `jq -r '.models[] | select(.slug=="<model>") | .max_context_window' ~/.codex/models_cache.json`. The displayed effective limit applies `effective_context_window_percent`; with 95 percent, 872,000 appears as 828K.

## A Codex update notice prevents agent start

An update prompt can make `agent start` return `agent_not_ready` (`blocked`). Read the options; to skip the update, send `herdr agent send-keys <name> 2`, then `enter` (Skip). Do not update without the user's instruction.

## Skill invocation differs by harness

Claude Code plugin skills use `/<plugin-name>:<skill-name>`; project and user skills use `/<skill-name>`. Codex, Antigravity (agy), and Gemini CLI use `$skill`; Pi uses `/skill:<skill>`.
Use the recipient harness's format in launch instructions. If the format has not been verified, provide the `SKILL.md` path and ask the agent to read it.

## Notices wait while the lead responds

The watcher defers lead delivery during `working`, `blocked`, or a detected Claude Code question screen. Once delivery is possible, it reevaluates current worker states and combines notices in one message.

**There is no deferral time limit.** Worker stops may not reach the lead during a long response or question. Human notices are separate: a `blocked` stop with confirmation markers triggers one immediately; other stops trigger one after delivery to the lead or two consecutive observations. The human operator should inspect time-sensitive worker confirmations directly.

## herdr reports a lead question screen as done

A named Claude Code session may show its session-name divider below a question, causing herdr to report `done` instead of `blocked` (herdrdev/herdr#4573). The watcher removes blank lines and examines only the last three lines. It defers without updating sent records when a line starts, allowing leading spaces, with `Enter to select` / `Enter to confirm` and contains `Esc to cancel` on the same line, or starts with `Esc to cancel`. It does not use `Do you want to proceed?` as a lead-screen marker.

Idle screens have an input field or footer at the bottom, so old copied worker screens in the conversation are ignored. After composing a notice, the watcher rereads the lead by name immediately before sending. It can do this even with an empty pane ID. A failed read also defers delivery, logs the failure, and retries the decision next round.

**This detection applies only to Claude Code leads. Pi lead question screens are not detected.** Inspect actual screens before adding markers.

## Unverified behavior

- **Confirmation markers outside Claude Code.** The worker markers (`Do you want to proceed?`, `Permission rule`, `❯ 1.`) come from Claude Code. Codex confirmation screens have not been verified on a live harness; do not add guessed markers. Without markers, persistent `blocked` is detected on the second observation (30–60 seconds at the default interval).
- **Prompts while a human types in the lead's input.** Whether herdr interrupts, overwrites, or appends to the input has not been verified.
- **done while waiting for background subagents.** Whether a screen with a background agent list such as `← 5 agents` enters `done` has not been verified on a live harness. Do not add subagent markers until verified.
- **Long-running hooks in blocked.** A hook that stays `blocked` for two observations can cause an awaiting-confirmation notice. No hook-specific handling exists; consider it when a concrete problem occurs.
