# Adding and retiring tools

Grow agent-graph by responding to concrete problems with small changes, rather than designing the entire system in advance.

## Stages

**Document knowledge → formalize a procedure after at least two inconsistent outcomes or failures → create a script if the procedure still varies.**

## Conditions for adding a tool

- Record supporting examples in an Issue or comment: when, which worker, and what varied.
- **Agents must not add tools to agent-graph on their own authority.** The lead proposes the addition; the user approves it.
- Local experimental scripts (for example in `~/.local/bin`) may be created freely. Once evidence supports them, propose promotion using these conditions.

## Tool constraints

- Each tool performs one operation. Launching one worker, from creating its tab to confirming receipt of its first brief, counts as one operation. `start.sh` owns that boundary; it does not prepare a worktree, start monitoring, or create a PR.
- Preparing one assigned Issue means reading its settings and GitHub content, preparing its worktree through project-owned setup, and returning a brief. It does not include launching the worker. The entry skill may compose independent preparation and startup operations.
- Do not encode a graph topology, role count, or role combination.
- Keep no ledger. Logs and PID files are allowed. A per-run startup log may record operation attempts, placement, session identity, and input hashes to prevent an ambiguous retry. It must not become an index of agents, parents, assignments, or message delivery.
- Do not depend on other agent-graph tools.

## Retirement

- When herdr or a harness provides the same function, remove the tool and retain any useful knowledge.
- Review and remove tools that are no longer used.

## Human authorization for Issue merges

The Issue entry can merge only the PR created by its configured operator GitHub account, on that person's latest explicit request for the same Issue in their language (for example, `Merge #N` or `#N をマージして`), after the configured administrator's current-head GitHub approval and required CI pass. The administrator's Approve authorizes the reviewed change; the operator's input requests this merge. Both are required. This is a scoped exception to having the administrator execute every merge, not permission to merge automatically or bypass harness confirmation.

The merge operation checks GitHub only when invoked. It never polls, enables auto-merge, uses administrator bypass, or merges another account's PR. A harness permission screen still applies: inspect its exact scope, explain it in the operator's language, and never change settings or switch commands to avoid a rejection.
