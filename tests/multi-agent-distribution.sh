#!/usr/bin/env bash
# Keeps the per-skill agent support flag consistent with how each skill is
# distributed.
#
# Each SKILL.md records the agents it has been verified with in
# `metadata.agent-tools-agents` (a space-separated subset of "claude codex pi").
# From that flag:
# - Claude-only skills carry `compatibility: 'Designed for Claude Code'`;
#   other skills must not.
# - Skills without `codex` ship agents/openai.yaml with
#   `policy.allow_implicit_invocation: false`, so Codex never invokes them on
#   its own; skills with `codex` must not ship that restriction.
# - package.json `pi.skills` lists exactly the skills whose flag includes `pi`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
yaml_python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import yaml' >/dev/null 2>&1; then
    yaml_python="$candidate"; break
  fi
done
[[ -n "$yaml_python" ]] || { echo 'not ok PyYAML is required (python3 -m pip install pyyaml)' >&2; exit 1; }
"$yaml_python" - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1]).resolve()
errors = []
KNOWN = {"claude", "codex", "pi"}

def check(condition, message):
    print(("ok " if condition else "not ok ") + message)
    if not condition:
        errors.append(message)

def frontmatter(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end]))

skills = sorted((root / "plugins").glob("*/skills/*/SKILL.md"))
check(bool(skills), "found skills to check")
pi_paths = set()
for path in skills:
    label = str(path.parent.relative_to(root))
    try:
        data = frontmatter(path)
        meta = data.get("metadata") if isinstance(data, dict) else None
        value = meta.get("agent-tools-agents") if isinstance(meta, dict) else None
        agents = value.split() if isinstance(value, str) else []
        valid = bool(agents) and len(agents) == len(set(agents)) and set(agents) <= KNOWN and "claude" in agents
        check(valid, f"{label}: agent-tools-agents is a valid list including claude ({value!r})")
        if not valid:
            continue
        if "pi" in agents:
            pi_paths.add(path.parent.resolve())
        if agents == ["claude"]:
            check(data.get("compatibility") == "Designed for Claude Code", f"{label}: Claude-only skill declares compatibility")
        else:
            check("compatibility" not in data, f"{label}: no Claude-only compatibility on a multi-agent skill")
        policy_path = path.parent / "agents/openai.yaml"
        if "codex" in agents:
            if policy_path.exists():
                policy = yaml.safe_load(policy_path.read_text()) or {}
                blocked = isinstance(policy.get("policy"), dict) and policy["policy"].get("allow_implicit_invocation") is False
                check(not blocked, f"{label}: verified on Codex, so implicit invocation is not blocked")
            else:
                check(True, f"{label}: verified on Codex, no invocation restriction")
        else:
            policy = yaml.safe_load(policy_path.read_text()) if policy_path.exists() else None
            check(isinstance(policy, dict) and isinstance(policy.get("policy"), dict)
                  and policy["policy"].get("allow_implicit_invocation") is False,
                  f"{label}: not verified on Codex, so agents/openai.yaml blocks implicit invocation")
    except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError) as exc:
        check(False, f"{label}: failed to read ({exc})")

try:
    package = json.loads((root / "package.json").read_text())
    listed = package["pi"]["skills"]
    check(isinstance(listed, list) and all(isinstance(i, str) for i in listed), "pi.skills is a list of strings")
    listed_paths = set()
    for item in listed:
        p = (root / item).resolve()
        check(item.startswith("./plugins/") and p.is_relative_to(root) and (p / "SKILL.md").is_file(),
              f"pi.skills: {item} is an existing skill directory")
        listed_paths.add(p)
    check(len(listed_paths) == len(listed), "pi.skills has no duplicates")
    check(listed_paths == pi_paths, "pi.skills matches exactly the skills flagged with pi")
except (OSError, ValueError, KeyError, TypeError) as exc:
    check(False, f"package.json: failed to read ({exc})")

print(f"\n# multi-agent-distribution: {len(errors)} failed")
sys.exit(bool(errors))
PY
