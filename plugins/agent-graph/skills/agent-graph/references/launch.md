# Harness launch arguments

Use this reference when starting a worker or lead with `herdr agent start` (see [herdr.md](herdr.md)).
**Do not maintain fixed lists of model names, reasoning levels, or context limits here.** Look them up before launching.

## Look up available values

```bash
# Codex: slug, description, context limit, and supported reasoning levels.
jq -r '.models[] | select(.visibility=="list") | "\(.slug)\t\(.description)\t\(.max_context_window)\t\(.supported_reasoning_levels|map(.effort)|join(","))"' ~/.codex/models_cache.json

# agy (Antigravity)
agy models

# Claude Code: read the --model and --effort options.
claude --help
```

Do not rank models by role here; those assessments become outdated. Before launching, **show the user a table of engine / model / reasoning level / context and obtain approval**.
Be explicit about the engine: "default model, medium reasoning" is ambiguous when several engines have different defaults.

## Claude Code

```bash
claude --dangerously-skip-permissions --model "<model-id>" --effort high -n "<display-name>" "<initial-instructions>"
```

- `--effort <low|medium|high|xhigh|max>`; consult `claude --help` for supported values.
- Request a 1M context with the model ID's `[1m]` suffix, where supported: `--model "<model-id>[1m]"`.
- `-n` sets the session display name in `/resume` and the tab title.
- Some commands still ask for permission under `--dangerously-skip-permissions`. See [pitfalls.md](pitfalls.md).

## Codex

```bash
codex --dangerously-bypass-approvals-and-sandbox -m <slug> -c model_reasoning_effort="high" "<initial-instructions>"
```

- Use a `slug` from `~/.codex/models_cache.json`. An invalid name fails rather than being corrected. Without a model argument, Codex uses `model` from `~/.codex/config.toml`.
- Set reasoning with `-c model_reasoning_effort=<value>`. Look up supported values in `supported_reasoning_levels` above.
- **The context limit is not applied through `-c`.** Set it in `~/.codex/config.toml`; see [pitfalls.md](pitfalls.md).

## agy (Antigravity)

```bash
agy --dangerously-skip-permissions --model "<name-from-agy-models>" --effort high -i "<initial-instructions>"
```

- Automatic approval uses `--dangerously-skip-permissions`.
- Reasoning uses `--effort <low|medium|high>`. Some model names also include a suffix such as `-high`.
- Pass initial instructions with `-i` (`--prompt-interactive`) to remain interactive after the first turn.
- For Gemini CLI: `gemini --yolo -m <model> -i "<initial-instructions>"`.

## Pi

Combine model and thinking in `--model <provider>/<id>:<thinking>`.

```bash
herdr agent start <name> --kind pi --pane "$PANE" -- --model "<provider>/<model-id>:medium"
```

- After launch, inspect the footer's model/thinking display to verify both values.
- Anthropic subscription authentication can show a usage-based billing warning at startup. Read the warning for the authentication method in use.

### Use Pi as lead

Pi does not have every tool available in Claude Code.

| Situation | Claude Code | Pi alternative |
|---|---|---|
| Ask the user | AskUserQuestion | Write numbered options in the response and wait for an answer |
| Wait for completion | Background task | Use `herdr agent wait`; receive reports through files rather than a Monitor tool |

## Verify after launch

Use `herdr agent read <name> --lines 20` to check the displayed model and reasoning level. **If they differ from the requested values, report the mismatch and restart with the correct arguments.**
