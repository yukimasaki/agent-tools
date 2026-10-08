## Summary

<!-- What this PR changes and why. -->

## Related issues

<!-- e.g. "Closes #12" -->

## Checks

- [ ] All checks pass: `status=0; for t in tests/*.sh; do bash "$t" || { echo "FAILED: $t"; status=1; }; done; [ "$status" -eq 0 ]`
- [ ] `claude plugin validate .` and `claude plugin validate ./plugins/<name>` pass

## Verified on

<!-- Which harnesses did you try the change on? Describe the scenario. -->

- [ ] Claude Code:
- [ ] Codex:
- [ ] Pi:

If the verified agents changed, `metadata.agent-tools-agents`, `pi.skills`, `compatibility`, and `agents/openai.yaml` are updated together.

## Checklist

- [ ] No machine-specific values (paths, ports, project names) were added
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
