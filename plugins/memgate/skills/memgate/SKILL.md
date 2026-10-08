---
name: memgate
description: 'Coordinate memory use by parallel agents on Linux and WSL: gate heavy launches, limit concurrent commands, and notify a herdr coordinator. Triggers: memgate, memory gate, メモリ監視, メモリ不足, メモリの取り決め.'
metadata:
  agent-tools-agents: 'claude'
compatibility: 'Designed for Claude Code'
---

# memgate

Use the bundled `scripts/memgate` to coordinate memory use across herdr
workspaces. It supports Linux and WSL only. It requires Python 3.11 or newer
with the standard library; monitoring uses `/proc`, `ss`, and herdr. Docker
inspection is optional. Missing herdr or Docker data limits attribution;
memory gates and concurrency slots still work without those tools.

## Commands and configuration

Resolve `scripts/memgate` relative to this skill directory. Use its full path,
or add that directory to PATH before using these examples:

```bash
memgate status
memgate gate 2500
memgate run --slot browser-tests --max 2 --need 2500 --wait 120 -- test-command
memgate slots
memgate loop coordinator-agent
```

The memory estimates above are illustrative, not measurements for a particular
machine. Choose an estimate from the workload's measured peak usage.

Configuration is `$XDG_CONFIG_HOME/memgate/config.toml`, falling back to
`~/.config/memgate/config.toml`. Start with [config.example.toml](config.example.toml)
and choose thresholds, repository roots, and protections for the current machine.
The example contains fictional values. Without a file, defaults are WARN below
4096MB, CRIT below 2048MB, and PSI `some avg10` at 8. Reports display the actual
configuration. MB means binary MiB throughout the tool.

State goes to `$XDG_STATE_HOME/memgate/`, falling back to
`~/.local/state/memgate/`. All callers sharing a concurrency limit must use the
same state directory. Settings are loaded at startup; restart a loop after
changing its configuration.

- `gate <MB>` exits 0 when the projected available memory stays at or above
  `critical_mb` and PSI is below `psi_threshold`. It exits 1 when launch should
  wait, and 2 on invalid input or an unreadable memory snapshot. A gate checks
  current memory; it does not reserve memory for future launches.
- `run` acquires a numbered lock, applies the same gate, then replaces itself
  with the command. It exits 75 without launching when `--wait` expires. It
  forwards the executed command's exit status. `--need` and `--wait` default to
  `run_need_mb` and `run_wait_seconds`. A zero wait permits one immediate attempt.
- A slot group has one fixed `--max`. A conflicting limit exits 2; use a new
  group name when changing the limit. Do not delete or copy live lock files.
  Descendants retain the slot while they inherit its open descriptor; programs
  that close inherited descriptors can release it sooner. Keep long-running
  work in the foreground when the slot must cover its entire lifetime.
- `loop <agent>` writes `status.md` and event reports, and prompts that existing
  herdr agent only while it is idle or done. One loop can hold the state
  directory lock. Stop it with the identified process's signal when finished.
  Notifications retry while the agent is busy and discard conditions that
  resolve before delivery. A recovery from a previously delivered warning is
  reported. Persistent warnings repeat after `reminder_seconds`.

The collector prefers a unique cwd owner over inherited `HERDR_WORKSPACE_ID`.
It detects repositories under configured `repo_roots`, or by walking up to a
`.git` directory or worktree file. Ambiguous cwd ownership retains the inherited
workspace. `ss` may hide processes owned by other users without sufficient
permissions; the script does not request elevated permissions by default.

## Agreement for workspace leads

Copy the worker text below into each worker's brief. Before launching a dev
server, build, browser test, E2E suite, or local container stack, pass a memory
gate. Use a shared `run` slot group when several agents may start that workload
at once. Keep one dev server per worktree and reuse an existing server.

If the gate refuses a launch, clean up completed workloads in your workspace
and retry. If it still refuses, report the requirement and current memory to
the designated coordinator. When the coordinator requests cleanup, pause new
heavy launches, release unused resources, and report the result.

Workers must stop the background servers, runners, browsers, and container
stacks they started before reporting completion. Stop a worktree's containers
before removing the worktree. Identify a process by PID, command, and cwd before
stopping it; avoid broad command-name kills.

The coordinator reviews `status` and event reports. Closed-workspace processes,
WSL orphans, idle servers, duplicate servers, and Docker projects without panes
are candidates for review, not proof that they can be stopped. Verify ownership
and dependencies. Stop only workloads within the user's authorized scope;
ask the workspace lead about servers awaiting human review. Protected ports,
repositories, and services are excluded from these candidates.

Docker candidates skip containers younger than `orphan_age_seconds` and
auto-removed (`docker run --rm`) containers younger than `idle_age_seconds`,
because these usually clean up after themselves. While the level is OK, a
Docker project is reported only when it has run for at least
`idle_age_seconds` and, once Docker stats are collected, uses at least
`min_rss_mb`.

The script never stops processes or containers. OOM score changes are disabled
unless `oom_score_adj` is explicitly configured. An optional `oom_use_sudo`
setting requests noninteractive `sudo choom` for protected processes. Use
`ignore_patterns` to suppress known retained workloads by their suspect IDs.
Do not treat ignore patterns as OOM protection.

## Text to copy into worker briefs

> Before any heavy launch, run `memgate gate <estimated-MB>`. Launch only on
> exit 0. On any other exit, do not launch: release your unused workloads and
> retry, or report the blocker to the lead. Use the lead's shared
> `memgate run --slot <group> --max <limit> --need <estimated-MB> --wait <seconds>
> -- <command>` when a concurrency limit applies. Reuse the worktree's existing
> dev server. Before reporting completion, stop background servers, runners,
> browsers, and container stacks you started. Identify targets by PID, command,
> and cwd; preserve protected workloads. Report unresolved memory needs to the
> lead, who coordinates with the designated memory coordinator.

## WSL

When running on WSL, read [references/wsl.md](references/wsl.md) for VM memory,
Docker Desktop, `/init` orphans, and earlyoom configuration. Idle and duplicate
server candidates are only reported at WARN or CRIT. MCP descendants within
four ancestors are excluded, and idle-server candidates must meet
`min_rss_mb` even if a small server has an open port.
