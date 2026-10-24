# Launch one worker and confirm its brief

`start.sh` requires Python 3.11 or newer and a herdr-managed caller (`HERDR_ENV=1`). It performs one operation: create a tab, launch one agent, and confirm receipt of its first brief. It never starts a watcher, prepares a worktree, runs a review, pushes, or creates a PR.

## Call

Write a brief that names the report file, work boundary, checks, and cleanup requirements. Use absolute paths for artifacts. Choose a unique run ID and keep the same paths and native arguments when resuming that run.

```bash
bash <skill-directory>/scripts/start.sh \
  --workspace "$HERDR_WORKSPACE_ID" --cwd <absolute-worktree-path> \
  --name impl-demo42 --kind claude \
  --brief <absolute-brief-path> --report <absolute-report-path> \
  --receipt <absolute-receipt-path> --run-id demo42-a \
  --timeout 120000 -- <approved-native-arguments>
```

Do not put an executable name or an initial prompt after `--`. The helper sends the brief after startup. Use approved arguments from [launch.md](launch.md); the helper does not select a model or permission mode.

The worker must read the entire brief, compute its SHA-256, then atomically write this receipt before starting work:

```json
{"run_id":"demo42-a","brief_sha256":"<sha256-of-the-brief>"}
```

Write a temporary file beside the receipt and rename it into place. Receipt confirms instructions, not completion. The lead still reads the report and checks results.

## Results

The last stdout line is JSON with `result`, `run_id`, `workspace`, `tab`, `pane`, `name`, `kind`, `cwd`, `brief`, `receipt`, and `report` when those values are known. Failures include `message`. Argument-parser errors print usage instead. Stderr has a short diagnostic. Failed calls leave their tab and files available for inspection.

| Exit | Result | Next action |
|---|---|---|
| 0 | `started` | Matching receipt confirmed; let the worker proceed |
| 2 | `invalid` | Correct arguments or prerequisites before retrying |
| 3 | `conflict` | Inspect the named agent, placement, or changed run inputs; do not overwrite |
| 4 | `needs_confirmation` | Read the pane and resolve the displayed confirmation with the required authorization |
| 5 | `unverified` or `instruction_unconfirmed` | Inspect the pane and receipt; do not repeat an ambiguous launch or prompt |

## Resume safely

Pass the same arguments and `--pane <returned-pane-id>`. This option only resumes a pane recorded by that run. It cannot adopt an arbitrary existing pane. A matching receipt returns success without another prompt. Changed brief bytes, native arguments, placement, or agent session cause a stop. When a harness has not published a session ID before its first prompt, the helper pins the terminal, foreground process-group PID, and process start time instead. It keeps that identity when the session ID appears later.

The receipt's sibling `<receipt>.start.jsonl` contains operation attempts, placement, session identity, and hashes. Keep it with the receipt. The helper records attempts before tab creation, agent startup, and prompt submission. If a response is lost, absence of success is not proof that the operation did not happen. It never creates another tab or agent, or resends a prompt, to guess its way past an ambiguous attempt.

A confirmation before prompt delivery can be resolved and resumed. An already attempted prompt without a matching receipt needs manual inspection; starting a new run is safe only after the lead has established what happened. Corrupt logs stop resumption. A per-run lock prevents concurrent launches for that run. Logs contain no raw native arguments and do not index agents or parent relationships.

## Harness limits

Before submitting instructions, the automated startup screen check requires a current empty Claude Code `❯` or Codex `›` input with a recognized footer and idle/done status. The observed Claude startup `Try "..."` placeholder also counts as empty input. Historical input markers above an unknown current screen do not count. After submission, the helper waits for receipt without requiring the busy renderer to look like an empty input field. These contracts have fake-herdr tests; record live verification separately. Other harness screens, empty screens, unknown states, and unreadable screens return unverified. The helper does not infer OpenCode readiness from its unreliable done status. Use the documented manual launch procedure for unverified harnesses.

A timeout covers startup, observation, and receipt waiting together. Inspect the actual model and reasoning after launch as required by [launch.md](launch.md). The receipt does not certify that the requested model was selected. The helper does not change permissions, answer trust questions, install updates, or silently switch engines.
