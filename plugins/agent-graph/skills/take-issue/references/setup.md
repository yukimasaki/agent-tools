# Administrator setup for Issue entry

The operator enters an assigned Issue number in an existing concern lead workspace. The administrator prepares authentication, project rules, and the exact launch profile once. The entry skill never creates Issue workspaces or changes assignments. People communicate PR notices between machines.

## Project configuration

Copy [project.example.json](project.example.json) into the trusted main checkout as `.agent-graph/project.json`. Set the repository, short project name, base branch, relative worktree root, and executable preparation script. For merge entry, set every required check name in `required_checks` and a `merge_method` of `squash`, `merge`, or `rebase`. Empty required checks cannot disable the merge gate. Do not add secrets, machine-specific absolute paths, launch arguments, or authentication fields.

`project` is a short lowercase identifier of up to 12 characters. Issue 42 gets a worker named `impl-demo42` and a worktree under the configured root named `demo42`. The optional `branch_template` controls generated names. Its default is `{type}/{number}{suffix}`, producing `type/42-english-slug` from the Issue title with `feat` as the default type. Supported placeholders are `{type}`, `{number}`, `{slug}` (up to 40 lowercase ASCII characters), and `{suffix}` (a hyphen plus the slug, or empty when there is no slug). For example, `feature/ABC-{number}{suffix}` produces `feature/ABC-42-search`. No attribute access, format specifications, or conversions are supported. An explicit `--branch` overrides generation and can use any literal valid Git branch name, such as `feature/ABC-42-search`. Both paths use `git check-ref-format --branch` and the existing branch/worktree checks; checkout shorthand such as `@{-1}` is rejected. Lead names can be `lead-demo`, `lead-demo-rec`, or `lead-demo-ui`; each concern gets its own workspace and watcher.

The preparation command is resolved inside the main checkout, including when the caller is in a linked worktree. Absolute paths and symlinks escaping the main checkout are rejected. The administrator must review the script. It receives argv elements with this contract:

```text
<configured-script> --repo <owner/repo> --repo-root <main-checkout>
  --issue <number> --branch <branch> --worktree <absolute-path> --run-id <id>
```

The script owns dependency installation, env files, certificates, ports, mutable state locations, and project-specific checks. Make retries safe. Do not overwrite existing work indiscriminately. Apply the project's memory gate before heavy work. GitHub tokens used by preparation are not passed to this script. On a preparation timeout, the helper stops that script's own process group before releasing its run lock. Preparation scripts must not daemonize detached services; project-owned services need their own cleanup contract.

The generic helper does not supply a production preparation script. A project can first test this contract with a small script before implementing its real setup.

## Machine profile and approval

Copy [machine.example.json](machine.example.json) to `~/.config/agent-graph/projects/<project>.json`. `XDG_CONFIG_HOME` changes the config root; `AGENT_GRAPH_CONFIG_ROOT` can explicitly select the agent-graph configuration directory. Authenticate the configured `github_user` before using the entry skill. Set `reviewer` to the administrator's GitHub login. That administrator approves; the operator account creates and requests merging its own PRs.

Choose the engine, model, reasoning, and permission arguments from the installed harness help. Store native arguments as a JSON array, never a shell command. The helper passes no executable name or initial task through this array. Review the exact profile with the administrator. Record the administrator and date, and compute the hash of the approved `launch` object:

```bash
python3 - <machine-profile-path> <<'PY'
import hashlib
import json
import sys
from pathlib import Path

profile = json.loads(Path(sys.argv[1]).read_text())
print(hashlib.sha256(json.dumps(profile["launch"], sort_keys=True).encode()).hexdigest())
PY
```

Put the printed value in `approval.launch_sha256` after approval. This hash detects an overlooked change; it is not an authentication signature. New engines and broader permissions require fresh approval and a new hash. Do not treat a hash recomputation as permission to approve a profile yourself. Ordinary Issue operators reuse the approved profile without choosing it again.

A matching configured account is required even when `GH_TOKEN` or `GITHUB_TOKEN` exists. The helper resolves one token and pins it for GitHub reads and HTTPS fetch without changing the machine-wide active account. All authentication lookup and API calls pin `GH_HOST=github.com`; a stale enterprise-host environment cannot redirect them. SSH origin uses the existing SSH authentication. Supported origin forms are credential-free `https://github.com/owner/repo.git` and `git@github.com:owner/repo.git`. Other hosts and SSH aliases need separate verification and are rejected. Tokens belong in credential storage or the process environment, not profiles, argv, briefs, or logs.

## Preparation output and recovery

```text
prepare-issue.sh --project <checkout> --workspace <id> --issue '#42'
  [--branch <literal-git-branch>] [--run-id <id>] [--prepare-timeout <seconds>]
```

The result is JSON. Success is `result: prepared`, with workspace, cwd, worker name, kind, native_args, brief, receipt, report, run_id, branch, base, repo, github_user, reviewer, Issue title/URL, and machine-profile path. It does not mean a worker started. Failure is `result: stopped`, with `message` and any known paths. Argument-parser errors print usage instead.

Exit 2 means invalid configuration, approval, authentication, or input. Exit 3 means an inaccessible/closed Issue or repository/worktree/run conflict. Exit 5 means preparation or an external operation did not finish. Existing worktrees are retained. A closed Issue is never silently reopened or started.

The default run ID is the short project name plus Issue number. Artifacts live under `<state-root>/<workspace>/<project>/<run-id>/`, where state root is `AGENT_GRAPH_STATE_ROOT` or `~/.local/state/agent-graph`. The helper keeps a brief and per-run preparation log, not a session ledger. Matching worktrees are reused. Completed project preparation is not repeated; failed preparation may be retried because the project script owns retry safety. Changed Issue content, launch profile, project config, script bytes, or run inputs stop the same run instead of changing an active brief.

Use a new `--run-id` only after inspecting the prior work and agent. A new run does not grant permission to launch a second worker with the same name. `start.sh` still checks the live agent and run evidence. Preparation and startup never call each other; the skill coordinates them.

## Monitoring and acceptance

Name the concern lead before normal use. Use `watch.sh --ensure <lead-name>` to start or reuse that workspace's watcher without replacing another lead's watcher. `watch.sh --ready <lead-name>` is a read-only check of the current PID, process start time, lead, and successful first observation. An old watcher without a matching readiness record needs its owner to restart it; readiness never starts or replaces a watcher.

Test in a dedicated workspace: enter an assigned number, verify preparation, receipt, report, and the stop notice. Then remove only the test's worktree, tabs, watcher, and temporary files. Cross-machine automation is outside scope. Repeat acceptance on each target machine. OpenCode startup and receipt are supported; its stop monitoring still requires the report-file wait below.

## Operator merge entry

Configure the administrator reviewer, required CI names, and project merge method before the normal two-command workflow. See [merge.md](merge.md) for explicit requests in the operator's language, such as `Merge #N` or `#N をマージして`, current-head approval, own-account PR restriction, and additional harness confirmations.


## OpenCode leads and workers

OpenCode 2.1.4 can load this skill and launch an OpenCode worker through `start.sh`. Install real copies of both sibling directories from the same plugin version:

```text
<skill-root>/
  take-issue/SKILL.md
  take-issue/references/...
  agent-graph/SKILL.md
  agent-graph/scripts/...
  agent-graph/references/...
```

Copy the entire directories, including scripts and references. `take-issue` resolves `../agent-graph`; copying only its `SKILL.md` breaks preparation and startup. Replace both copies together after upgrading the plugin. A plugin cache directory alone is not an OpenCode search path. A symlink-only installation failed in the live test; use real copies and verify discovery.

The [OpenCode skills reference](https://dev.opencode.ai/v2/docs/skills/) lists `.opencode/skills`, `.agents/skills`, and `.claude/skills` as project sources. Global sources include `~/.config/opencode/skills`, `~/.agents/skills`, and `~/.claude/skills`. An explicit source avoids relying on cache discovery:

```json
{
  "model": "<provider>/<approved-lead-model>",
  "skills": ["~/.config/opencode/skills"]
}
```

The `skills` array points to the parent containing both skill directories. Relative entries resolve from the active working directory, so use a home-relative or absolute source for linked worktrees. Load `@take-issue` and confirm its base directory before entering `Start #42`. If discovery fails, provide the copied `SKILL.md` path and have the lead read it.

### Approve the effective OpenCode configuration

The launch hash continues to cover only the exact `launch` object. It does not cover `opencode.json`, `opencode.jsonc`, inherited user configuration, or server state. Hashing a single project file would imply coverage of an effective configuration that can also depend on other sources. The helper does not resolve OpenCode's configuration merge rules.

For an OpenCode profile, the administrator must separately approve the lead and worker models and effective permission settings. Record that approval outside the repository with the machine profile. Use `launch.kind: "opencode"` and an approved argument array, usually `[]`; `--auto` requires explicit approval. Do not put `--model` in interactive arguments. The project preparation script must create the approved worker `opencode.json` in each worktree and stop on an existing conflicting file. Exclude the file from git using the common git directory's `info/exclude`.

Before startup, the lead compares the generated worker configuration with the approved configuration and checks applicable inherited configuration. After startup, the lead reads the actual model and permission display. A different or unavailable display stops acceptance of the launch and requires inspection. Receipt alone certifies neither model nor permissions. A configuration change requires administrator review even if `approval.launch_sha256` still matches. Do not infer approval from a recomputed hash.

### Wait for an OpenCode report

Keep the ordinary watcher for workspace readiness, but treat OpenCode status notices as prompts to inspect. They cannot certify completion. After a confirmed receipt, use the bounded report-file wait in [pitfalls.md](../../agent-graph/references/pitfalls.md#herdr-reports-a-working-opencode-as-done). Inspect the report and actual pane before reporting the result. This also applies to an OpenCode lead: herdr can misread its working state, so avoid sending notices into a lead that is still handling the request. Issue #13 tracks the remaining watcher work.

For acceptance, use a disposable local repository and a fake `gh` that allows only identity and Issue reads. Verify `Start #N`, receipt, worker report, and the lead reading that report. Never run the PR handoff in this fixture. Remove its watcher, tabs, worktrees, and temporary files afterward.
