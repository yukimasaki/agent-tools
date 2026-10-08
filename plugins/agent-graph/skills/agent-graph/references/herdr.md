# herdr operations

The commands below use the herdr 0.9.3 interface.

## Create a tab

```bash
OUT=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd <working-directory> --label "<display-name>" --no-focus)
PANE=$(echo "$OUT" | jq -r '.result.root_pane.pane_id')
```

- Always pass `--workspace "$HERDR_WORKSPACE_ID"`. Otherwise the tab is created in the active workspace and can interfere with unrelated work.
- Use `--no-focus` so the user's view of another workspace is not interrupted.
- The pane ID is `result.root_pane.pane_id`, not `result.pane`.
- Rename a tab with `herdr tab rename <tab_id> <label>`. Read its label from `herdr tab get <tab_id>` at `result.tab.label`, or list tabs with `herdr tab list --workspace <id>`.

## Start an agent

```bash
herdr agent start <name> --kind <claude|codex|agy|pi|…> --pane "$PANE" [--timeout <ms>] -- <harness-arguments>
```

- The pane must be at an interactive shell prompt. The command detects the agent and waits until it accepts input (default 30 seconds, maximum 300 seconds).
- See [launch.md](launch.md) for arguments. Prefer `agent start` to running a raw command with `herdr pane run`, which does not detect or name the agent.
- To name an agent started manually, use `herdr agent rename <pane_id> <name>`. Clear its name with `herdr agent rename <pane_id> --clear`.

## Send, wait, and read

| Operation | Command |
|---|---|
| Send instructions | `herdr agent prompt <name> "<text>"`; `--wait` waits for the turn to end, so omit it for long work |
| Wait for state | `herdr agent wait <name> --until <idle\|working\|blocked\|done\|unknown> [--until …] --timeout <ms>`; without `--until`, waits for `idle` / `done` / `blocked` |
| Read the screen | `herdr agent read <name> [--lines N] [--source visible\|recent\|recent-unwrapped\|detection]`; default source is `recent` |
| Send keys | `herdr agent send-keys <name> <keys…>`; for example `2`, then `enter` |
| List or get | `herdr agent list` / `herdr agent get <name-or-pane_id>` |

- **`herdr agent prompt` fails with `agent_blocked` when the recipient is `blocked`.** Inspect the worker's screen before deciding how to proceed.
- Output from agents using an alternate screen may not be available in scrollback through `agent read`. Assign a Markdown report file and ask the worker to end its turn after writing it. The lead reads it after the watcher sends a notice.

## Answer a confirmation

Read the agent's screen first and use the response format it requests. For a Claude Code permission screen (`Do you want to proceed?`, with `1. Yes / 2. … / 3. No`), send the option number.

```bash
herdr pane run <pane_id> "2"      # Sends the number and Enter together.
herdr agent prompt <worker> "<reason-and-revision-instructions>"
```

- Before answering, always run `herdr agent read <pane> --lines 12` to inspect the requested permission.
- Merges (`gh pr merge`), force pushes, and `rm -rf` depend on human authorization. The lead inspects the action before answering. Do not build automatic approval for these actions.

## Status semantics

- `agent_status` is `idle` / `working` / `blocked` / `done` / `unknown`.
- `herdr agent list` fields: `agent`, `agent_status`, `name` (absent for unnamed agents), `pane_id`, `tab_id`, `workspace_id`, `cwd`, `terminal_title`, `state_change_seq`, and `completion_seq` (only agents that have completed a turn).
- **`state_change_seq` is allocated globally, but each agent retains the sequence of its own last transition.** Another agent's transitions do not change it. Compare only equality, not the size of an increment. `done -> idle` when a user views a tab changes neither `state_change_seq` nor `completion_seq`.
- `completion_seq` changes when that agent completes a turn and is null during `working`; it is absent before the first completion. `watch.sh` uses only `state_change_seq`.
- Claude Code may enter `done` while a background shell is still running. The screen can show `· done <time> · 1 shell still running` and a footer containing `· 1 shell`. Other harnesses' displays are unverified; inspect them rather than assuming the same footer.
- Closing a workspace removes its agents. `agent get` returns `agent_not_found`; `workspace get` returns `workspace_not_found`. There is no `gone` status.
- Human notification command: `herdr notification show "<title>" [--body <text>] [--position <top-left|top-right|bottom-left|bottom-right>] [--sound <none|done|request>]`.
