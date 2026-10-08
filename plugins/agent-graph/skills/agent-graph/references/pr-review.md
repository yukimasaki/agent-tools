# PR and review sequence

Implement → test → review with Claude Code and Codex → create the PR.

1. **Implement and test.** Run local tests and the validation required by the repository, then make the worktree clean.
2. **Claude Code review.** Use the built-in `/code-review`; this skill provides no wrapper.
3. **Codex review.** Run `bash <skill-directory>/scripts/review.sh`, read the resulting file, and resolve findings. Use `--uncommitted` for changes that have not been committed.
4. After fixing findings, rerun the tests from step 1 and confirm they pass.
5. **Create the PR.** Run `bash <skill-directory>/scripts/pr.sh --title "<title>" --body-file <absolute-path> [--gh-user <account>]`. The default is ready; pass `--draft` only when a draft is wanted. Without `--gh-user`, the script resolves the environment token's owner or gh's active account once, then pins that token for gh operations and HTTPS GitHub pushes.

Follow the repository's rules (such as `.claude/rules/`) for waiting, merge conditions, and PR review bots.

## Why review.sh resolves the base

The script fetches `origin main` and uses `git merge-base origin/main HEAD` as the review base. With `--base`, it uses that branch instead. Choosing the wrong base in a worktree can include unrelated, already merged changes in the review. Resolving the merge-base avoids that mistake.

## Focused reviews

`review.sh` accepts no criteria, model, or effort arguments. Codex's built-in `review-agent` skill (`~/.codex/skills/.system/review-agent/SKILL.md`) supplies review criteria. For a focused review, invoke Codex directly:

```bash
codex exec review "Review the diff against main for <criteria>"
```

- **`--base` and a prompt cannot be combined** (`the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`). Describe the comparison target in the prompt.
- `codex review` / `codex exec review` starts with `sandbox: read-only` without needing an explicit setting.
- Use `-o <file>` to save only the final response. Standard output may show the response twice; the output file avoids that duplication.
- To review a plan, pass its path for Codex to read: `codex exec -s read-only "Review the plan at <path>"`.
