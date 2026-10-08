# Adding and retiring tools

Grow agent-graph by responding to concrete problems with small changes, rather than designing the entire system in advance.

## Stages

**Document knowledge → formalize a procedure after at least two inconsistent outcomes or failures → create a script if the procedure still varies.**

## Conditions for adding a tool

- Record supporting examples in an Issue or comment: when, which worker, and what varied.
- **Agents must not add tools to agent-graph on their own authority.** The lead proposes the addition; the user approves it.
- Local experimental scripts (for example in `~/.local/bin`) may be created freely. Once evidence supports them, propose promotion using these conditions.

## Tool constraints

- Each tool performs one operation.
- Do not encode a graph topology, role count, or role combination.
- Keep no ledger. Logs and PID files are allowed.
- Do not depend on other agent-graph tools.

## Retirement

- When herdr or a harness provides the same function, remove the tool and retain any useful knowledge.
- Review and remove tools that are no longer used.
