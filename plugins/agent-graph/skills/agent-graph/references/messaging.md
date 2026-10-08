# Communication

Use only two delivery mechanisms: `herdr agent prompt` and files. Do not introduce a separate message transport.

## Lead to worker: instructions

- **Write instructions to a file and send the path.** Long command arguments are prone to quoting errors.

  ```bash
  herdr agent prompt <worker> "Read the brief and begin work: /abs/path/brief-1.md"
  ```

- Every brief must state:
  - The report file's absolute path, and that the worker must write it and end its turn without sending `herdr agent prompt` to the lead.
  - When a decision is needed, stop work, write a report, and end the turn.
  - Do not perform lead-owned actions such as push, PR creation, or Issue comments unless explicitly assigned.
  - Create temporary files with `mktemp -d` and remove them using absolute paths. `rm -rf *` after changing directories can trigger a permission prompt.
- Omit `--wait` for long work. Wait in bounded intervals with `herdr agent wait <name> --timeout <ms>` so the lead can continue talking with the user.
- **Tell workers to stop any background servers or shells they started before reporting.** Otherwise the watcher treats the stopped worker as waiting for a shell and does not wake the lead.
- When assigning CI monitoring, ask the worker to check for failed jobs during the run as well as waiting for all jobs to finish.

### When `herdr agent prompt` returns `agent_blocked`

The recipient may be awaiting confirmation or temporarily `blocked`, for example during a hook without a visible confirmation screen.

1. Read the pane with `herdr agent read <name> --lines 12`.
2. If it shows a confirmation (`Do you want to proceed?` or a `❯ 1.` option), inspect the requested permission and answer using `herdr pane run <pane> "<number>"`; see [herdr.md](herdr.md).
3. Without a confirmation screen, wait briefly (about 20 seconds) and retry. If two or three retries still return `blocked`, read the pane again.

## Worker to lead: report

- **Workers write the file specified by their brief and end their turn. They do not send `herdr agent prompt` to the lead.** The lead reads the report after `watch.sh` sends a notice, which can take one polling interval (default 30 seconds).
- `herdr agent prompt` sends text and Enter. If the lead is displaying a question, this can select an option before the user answers. The watcher checks Claude Code lead question screens and defers delivery until the question closes. This screen check does not apply to Pi leads.
- If a worker stops awaiting confirmation before reporting, the watcher also detects that stop and wakes the lead.

## Lead to lead: across workspaces

Send requests to another Epic lead with `herdr agent prompt lead-<other-epic-number>`.

- **Read `herdr agent read lead-<recipient> --lines 15` before sending**, including replies. If the screen shows selection instructions (`Esc to cancel` with `Enter to select` / `Enter to confirm`) or a permission confirmation, wait until the question closes.
- **Identify yourself first:** state your name (`lead-<N>`) and the Epic and topic in one or two lines.
- Put long messages in a file and send its path.
- The recipient replies with `herdr agent prompt lead-<N>`. State when a request is not urgent.
- Do not contact workers in other workspaces directly. Ask their lead when you need their help.

## Receiving notices

- Watcher prompts arrive as new messages for a Claude Code lead and can interrupt an ongoing response.
- By the time a notice arrives, the worker may already have received new instructions and resumed. Read the pane to confirm its current state before acting.
- Claude Code can stop a lead from using a long `sleep` during a response. Assign waiting work to a worker so the lead remains available.
