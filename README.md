**English** | [日本語](./README.ja.md)

# agent-tools

Tools for running a **graph of coding agents** on [herdr](https://herdr.dev): one lead agent per workspace that coordinates worker agents in separate tabs. The skills work with **Claude Code, Codex, and Pi**.

- **agent-graph** — naming, lead promotion and handoff, a watcher that wakes the lead when a worker stops, and small helpers for PRs and Codex reviews
- **memgate** — keeps many parallel agents from exhausting the machine's memory: gate heavy launches, cap concurrent runs, and alert a coordinator agent

The skills hold only minimal tools and the knowledge of *when to do what*. They do not ship review checklists, model rankings, or a fixed graph shape, so each part can be thrown away when the harnesses catch up.

## Requirements

- [herdr](https://herdr.dev) (the agents run inside herdr panes; `HERDR_ENV=1`)
- Bash 4 or newer, `jq`, `git`, and the [GitHub CLI](https://cli.github.com/) for the PR helper
- Python 3.11 or newer for memgate (standard library only)
- memgate supports **Linux and WSL** only (it reads `/proc` and PSI)

## Install

### Claude Code

```text
/plugin marketplace add yukimasaki/agent-tools
/plugin install agent-graph@agent-tools
/plugin install memgate@agent-tools
```

### Codex

Codex reads the same marketplace manifest:

```bash
codex plugin marketplace add yukimasaki/agent-tools
codex plugin add agent-graph@agent-tools
codex plugin add memgate@agent-tools
```

### Pi

```bash
pi install git:github.com/yukimasaki/agent-tools
```

Pi loads only the skills listed in `package.json` (`pi.skills`), which are the skills verified on Pi.

## Skills

Each skill records the agents it has been verified with in its `SKILL.md` frontmatter (`metadata.agent-tools-agents`). A skill not yet verified on Codex is never invoked implicitly by Codex, and Pi receives only the skills verified on Pi.

### agent-graph plugin

| Skill | Verified on | What it does |
|---|---|---|
| `agent-graph` | Claude Code, Codex, Pi | Lead and worker naming, lead promotion and handoff, `watch.sh` (wakes the lead when a worker stops or waits for approval), `pr.sh`, `review.sh`, launch arguments for claude / codex / agy / Pi |

### memgate plugin

| Skill | Verified on | What it does |
|---|---|---|
| `memgate` | Claude Code | `gate` before heavy launches, `run` with a concurrency slot, `status`, and a `loop` that alerts a coordinator agent when memory runs low and, when enabled, briefs new workspace leads and reports new workers |

## Configuration

Machine-specific values are not stored in this repository. memgate reads `~/.config/memgate/config.toml`; see `plugins/memgate/skills/memgate/config.example.toml`.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Security issues: [SECURITY.md](./SECURITY.md).

## License

[MIT](./LICENSE)
