**English** | [日本語](./CONTRIBUTING.ja.md)

# Contributing to agent-tools

Issues and PRs are welcome.

## Bug reports and feature requests

Use [GitHub Issues](https://github.com/yukimasaki/agent-tools/issues). The templates ask for the skill, the agent harness (Claude Code / Codex / Pi), and the herdr version.

## Changing a skill

- Keep the skill small: minimal tools plus the knowledge of when to use them. One tool does one operation and does not depend on other tools.
- Do not put machine-specific values (paths, ports, project names, thresholds) in the repository. Read them from a config file under `~/.config/<tool>/` and ship a `config.example.*` with made-up values.
- If you verify a skill on another agent, update these four together: `metadata.agent-tools-agents`, `pi.skills` in `package.json`, `compatibility` (Claude-only skills only), and `agents/openai.yaml` (skills not verified on Codex only).
- Do not translate strings that match another program's screen (approval prompts, footers) or herdr status values.

## Checks

Run all checks before opening a PR:

```bash
status=0; for t in tests/*.sh; do bash "$t" || { echo "FAILED: $t"; status=1; }; done; [ "$status" -eq 0 ]
```

The tests need `jq` and Python with PyYAML. The `claude plugin validate` part runs only when the claude CLI is installed.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) (Japanese is fine):

```text
feat(agent-graph): add a --dry-run option to pr.sh
fix(memgate): re-check the alert after recounting
```

## License

By submitting a PR, you agree that your contribution will be released under the [MIT License](./LICENSE).
