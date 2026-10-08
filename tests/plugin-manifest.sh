#!/usr/bin/env bash
# Checks the marketplace, plugin manifests, and SKILL.md frontmatter.
#
# A broken SKILL.md frontmatter does not stop the skill from loading: every
# field is silently dropped, so the skill loses its description and never
# triggers. `claude plugin validate` does not catch this, so the description is
# checked here. A plain (unquoted) YAML scalar breaks on ": " or " #", which is
# why descriptions must be quoted or written as a block scalar.
#
# Reads repository files only. Requires jq and Python with PyYAML. The
# `claude plugin validate` part is skipped when the claude CLI is missing.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0; FAIL=0; SKIP=0
ok()     { PASS=$((PASS+1)); printf 'ok %s\n' "$1"; }
not_ok() { FAIL=$((FAIL+1)); printf 'not ok %s\n' "$1" >&2; }
skip()   { SKIP=$((SKIP+1)); printf 'ok %s # SKIP %s\n' "$1" "$2"; }

# ---------- marketplace and plugin manifests ----------

MARKET="$ROOT/.claude-plugin/marketplace.json"
version="$(jq -r '.metadata.version' "$MARKET")"
pkg_version="$(jq -r '.version' "$ROOT/package.json")"
[[ "$pkg_version" == "$version" ]] && ok "package.json version matches the marketplace ($version)" \
  || not_ok "package.json version ($pkg_version) matches the marketplace ($version)"

listed="$(jq -r '.plugins[].name' "$MARKET" | sort)"
on_disk="$(for d in "$ROOT"/plugins/*/; do basename "$d"; done | sort)"
[[ "$listed" == "$on_disk" ]] && ok "marketplace lists exactly the plugins under plugins/" \
  || not_ok "marketplace lists exactly the plugins under plugins/ (listed: $(echo $listed), on disk: $(echo $on_disk))"

for d in "$ROOT"/plugins/*/; do
  name="$(basename "$d")"
  manifest="$d/.claude-plugin/plugin.json"
  if [[ ! -f "$manifest" ]]; then not_ok "$name: has .claude-plugin/plugin.json"; continue; fi
  [[ "$(jq -r '.name' "$manifest")" == "$name" ]] && ok "$name: plugin name matches its directory" \
    || not_ok "$name: plugin name matches its directory"
  [[ "$(jq -r '.version' "$manifest")" == "$version" ]] && ok "$name: plugin version matches the marketplace" \
    || not_ok "$name: plugin version matches the marketplace"
  source="$(jq -r --arg n "$name" '.plugins[] | select(.name == $n) | .source' "$MARKET")"
  [[ "$source" == "./plugins/$name" ]] && ok "$name: marketplace source points at the plugin" \
    || not_ok "$name: marketplace source points at the plugin ($source)"
  extra="$(jq -r --arg p "plugins/$name/.claude-plugin/plugin.json" '[.packages["."]["extra-files"][] | select(.path == $p)] | length' "$ROOT/release-please-config.json")"
  [[ "$extra" == 1 ]] && ok "$name: release-please bumps the plugin version" \
    || not_ok "$name: release-please bumps the plugin version"
  count=0
  for f in "$d"/skills/*/SKILL.md; do [[ -f "$f" ]] && count=$((count+1)); done
  [[ $count -gt 0 ]] && ok "$name: has at least one skill" || not_ok "$name: has at least one skill"
done

for p in .claude-plugin/marketplace.json package.json; do
  extra="$(jq -r --arg p "$p" '[.packages["."]["extra-files"][] | select(.path == $p)] | length' "$ROOT/release-please-config.json")"
  [[ "$extra" == 1 ]] && ok "release-please bumps $p" || not_ok "release-please bumps $p"
done

# ---------- SKILL.md frontmatter ----------

files=()
for f in "$ROOT"/plugins/*/skills/*/SKILL.md; do [[ -f "$f" ]] && files+=("$f"); done

yaml_python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import yaml' >/dev/null 2>&1; then
    yaml_python="$candidate"; break
  fi
done

if ((${#files[@]} == 0)); then
  not_ok "skills: found SKILL.md files to check"
elif [[ -z "$yaml_python" ]]; then
  not_ok "skills: frontmatter parses as YAML (PyYAML is required: python3 -m pip install pyyaml)"
elif "$yaml_python" - "${files[@]}" <<'PY'
from pathlib import Path
import sys
import yaml

failed = False
def fail(path, msg):
    global failed
    print(f"{path}: {msg}", file=sys.stderr)
    failed = True

for name in sys.argv[1:]:
    path = Path(name)
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        fail(path, "frontmatter must open and close with ---"); continue
    end = lines.index("---", 1)
    try:
        data = yaml.safe_load("\n".join(lines[1:end]) + "\n")
    except yaml.YAMLError as exc:
        fail(path, exc); continue
    if not isinstance(data, dict):
        fail(path, "frontmatter is not a mapping"); continue
    if data.get("name") != path.parent.name:
        fail(path, "name must match the skill directory")
    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        fail(path, "description is missing or empty"); continue
    raw = [l for l in lines[1:end] if l.startswith("description:")]
    if len(raw) != 1:
        fail(path, "description must appear exactly once"); continue
    value = raw[0].split(":", 1)[1].strip()
    if not (value[:1] in "'\"" or value[:1] in ">|"):
        fail(path, "description must be quoted or a block scalar")

raise SystemExit(1 if failed else 0)
PY
then
  ok "skills: frontmatter parses, name matches, description is quoted"
else
  not_ok "skills: frontmatter parses, name matches, description is quoted"
fi

# ---------- claude plugin validate ----------

# Pass each plugin directory: validating the repository root only checks the
# marketplace manifest and never reads the skills' frontmatter.
if ! command -v claude >/dev/null 2>&1; then
  skip "claude plugin validate" "claude CLI not found"
else
  if out="$(cd "$ROOT" && claude plugin validate . 2>&1)"; then ok "marketplace passes plugin validate"
  else not_ok "marketplace passes plugin validate"; printf '%s\n' "$out" | sed 's/^/    /' >&2; fi
  for d in "$ROOT"/plugins/*/; do
    name="$(basename "$d")"
    if out="$(cd "$ROOT" && claude plugin validate "./plugins/$name" 2>&1)"; then ok "$name: passes plugin validate"
    else not_ok "$name: passes plugin validate"; printf '%s\n' "$out" | sed 's/^/    /' >&2; fi
  done
fi

printf '\n# plugin-manifest: %d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
[[ $FAIL -eq 0 ]]
