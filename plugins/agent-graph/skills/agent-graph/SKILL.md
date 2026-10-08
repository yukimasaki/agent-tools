---
name: agent-graph
description: 'Use /agent-graph to coordinate agents in herdr, promote or hand off a lead, start workers, or contact another Epic lead. Triggers include 「lead に昇格して」「新しい lead に引き継いで」「作業役を立てて」「別の Epic の lead に連絡して」. Provides naming, handoff, stop monitoring, PR creation, Codex review, and harness launch procedures; excludes review criteria and model rankings.'
metadata:
  agent-tools-agents: 'claude codex pi'
---

# agent-graph

Use multiple AI agents in herdr tabs, with a lead coordinating workers.
Keep only small tools and knowledge about when to use them, so individual parts can be replaced as the tools change. This skill does not maintain:

- Review criteria or model rankings. Use each harness's built-in review support, such as Claude Code's `/code-review` and Codex's `review-agent`.
- Lists of model names or reasoning levels. Look up current values when launching.
- A ledger, persistent graph state, or a graph topology embedded in the tools.

Requirements: run inside herdr (`HERDR_ENV=1`); install `jq`. The watcher requires Bash 4 or newer.

## Graph structure

- **Workspace = Epic; tab = role.** Use one herdr workspace per Epic, including a single Issue or investigation. Give implementation, review, and separate investigations their own tabs.
- **Only two kinds of communication edges:** the workspace lead to its sessions, and lead to lead. Workers communicate through their lead rather than contacting other workers directly.
- Leads send instructions and contact other leads with `herdr agent prompt <name>`. Put long instructions in a file and send its path. Workers write their report file and end the turn; the lead reads it after the watcher sends a notice. See [messaging.md](references/messaging.md).

## Naming

Agent names must be unique throughout herdr: 1–32 characters, starting with a lowercase letter, using only lowercase letters, digits, `-`, and `_`. Names such as `172-lead` or `e172.lead` produce `invalid_agent_name`.

| Role | Agent name | Example |
|---|---|---|
| Active lead | `lead-<number>`, or `lead-<short-name>` for an investigation without a number | `lead-123`, `lead-research` |
| Previous lead after handoff | `lead-<number>-g<generation>` | `lead-123-g1` |
| Worker | `<role>-<issue-number>` | `impl-456`, `review-456` |

- The watcher treats every agent whose name does not start with `lead-` as a worker, including unnamed agents.
- If an Epic in another repository has the same number and causes `agent_name_taken`, stop and inform the user. See [pitfalls.md](references/pitfalls.md).

## Promote a tab to lead

Use a procedure rather than a script. When asked `/agent-graph lead <number>`, follow these steps whether creating a lead deliberately or promoting a tab that has become the lead during an investigation.

1. Check `herdr agent list` for an existing `lead-<number>`. If one exists, stop and explain the handoff procedure below.
2. Name your pane: `herdr agent rename "$HERDR_PANE_ID" lead-<number>`.
3. Label your tab: `herdr tab rename "$HERDR_TAB_ID" lead`.
4. Start monitoring: `bash <skill-directory>/scripts/watch.sh lead-<number>`.

Only one watcher runs per workspace. Starting another for the same lead returns success without doing anything.

## Hand off to a new lead

When the user asks to hand off the lead:

1. The old lead writes `~/.local/state/agent-graph/<workspace_id>/handoff-g<generation>.md`, using `$HERDR_WORKSPACE_ID`. Include the Issue, decisions, running workers and their state, unresolved questions, next steps, and applicable rules. **Include the instruction that the new lead must perform promotion steps 3–4: label its tab `lead` and start `watch.sh`.**
2. Start the new lead in a new tab and ask it to read the handoff. Follow [launch.md](references/launch.md), and include promotion steps 3–4 in its initial instructions. **Start it with a temporary name beginning with `lead-`**, such as `herdr agent start lead-<N>-next --kind claude --pane "$PANE" -- ...`. Without that prefix the watcher treats it as a worker and notifies the old lead when it finishes reading the handoff.
3. The old lead renames itself, relabels its tab, and renames the new lead **in one shell invocation, in this order**: `herdr agent rename "$HERDR_PANE_ID" lead-<N>-g<generation> && herdr tab rename "$HERDR_TAB_ID" lead-g<generation> && herdr agent rename <new-lead-pane> lead-<N>`. Distinct tab labels help the human operator identify the new lead. The order avoids `agent_name_taken` and minimizes the gap when the active name is vacant. The watcher accepts `lead-<N>-next` during this gap. The new lead performs promotion steps 3–4.
4. Keep the old lead available. **Do not close it automatically.** The user closes it after confirming the new lead is ready, so missing handoff details can still be recovered.

Workers continue writing to their assigned report paths after handoff. The watcher resolves the lead by name on each round, so it need not be stopped.

## Tools

| Tool | Usage | Behavior |
|---|---|---|
| `scripts/watch.sh` | `watch.sh <lead-agent-name>` | Polls workers in the workspace every 30 seconds. Wakes the lead and notifies the user when a worker is `blocked` or stops in `done` / `idle`. Holds only notification sequences, previous-round observations, process-exit notices, and notification retry counts. A `blocked` worker with confirmation markers triggers a human notice immediately; other stops trigger one after delivery to the lead or two consecutive observations. Failed human notices retry while the stop persists, giving up after three failures. Suppresses workers waiting for background shells (`· N shell` in the footer) and transient `blocked` states without confirmation markers. Also detects exited agents whose panes remain. Defers lead delivery during `working` / `blocked` or a detected Claude Code question screen, without marking the notice as sent. Re-reads the lead by name immediately before sending; failed reads also defer delivery. Replaces a watcher for a different lead in the same workspace. Exits when the workspace closes or the lead disappears. PID and log files live in `~/.local/state/agent-graph/<workspace_id>/`. |
| `scripts/pr.sh` | `pr.sh --title <title> --body-file <absolute-path> [--gh-user <account>] [--base main] [--draft]` | Requires a clean worktree, pushes, creates a ready PR, and prints its URL. Resolves authentication once and pins the token for gh operations and HTTPS GitHub pushes. Without `--gh-user`, uses the environment token's owner or gh's active account. Reuses an existing open PR for the branch and base, promoting a draft to ready unless `--draft` is requested. |
| `scripts/review.sh` | `review.sh [--uncommitted] [--base <branch>] [--out <file>]` | Runs `codex exec review` against the merge-base with `origin/<base>` (default `main`) and prints the output file path. |

- Watcher notices include the agent name, pane ID, tab label, status, classification (permission request / awaiting confirmation / stopped), and the last eight pane lines. They say "stopped" rather than "completed", because the worker may be waiting after a rejected confirmation. Read the report file when present; otherwise inspect the pane.
- See [herdr.md](references/herdr.md) for answering confirmations. The lead must inspect merges and force pushes before answering; do not approve them automatically.
- **Lead question-screen protection applies only to Claude Code.** The watcher does not detect Pi lead question screens. Do not assume equivalent protection in other harnesses.

## References

| File | Contents |
|---|---|
| [launch.md](references/launch.md) | Launch arguments for Claude Code, Codex, agy, and Pi; how to look up models and reasoning levels |
| [herdr.md](references/herdr.md) | Tabs, agent operations, confirmations, and status semantics |
| [messaging.md](references/messaging.md) | Instructions, reports, and communication with other Epic leads |
| [pr-review.md](references/pr-review.md) | Implementation → tests → review → PR; focused reviews |
| [pitfalls.md](references/pitfalls.md) | Operational pitfalls and limitations |
| [promotion.md](references/promotion.md) | Rules for adding and retiring tools |
