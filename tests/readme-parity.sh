#!/usr/bin/env bash
# Checks that every distributed skill is listed in both READMEs.
#
# Each README has a skills section with one subsection per plugin, and a table
# whose first column is `` | `skill-name` | ``. Matching is anchored to the first
# column of the plugin's own subsection, so a mention in another row or another
# plugin does not count as a listing.
#
#   README.md     -> "## Skills"  / "### <plugin> plugin"
#   README.ja.md  -> "## スキル" / "### <plugin> プラグイン"
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0; FAIL=0
ok()     { PASS=$((PASS+1)); printf 'ok %s\n' "$1"; }
not_ok() { FAIL=$((FAIL+1)); printf 'not ok %s\n' "$1" >&2; }

# $1=README file, $2=section heading, $3=plugin subsection heading
section() {
  awk -v h="$2" -v p="$3" '
    $0==h {in_s=1; next}
    in_s && /^## / {exit}
    in_s && /^### / {in_p = ($0==p)}
    in_s && in_p {print}
  ' "$1"
}

check_readme() {
  local readme="$1" heading="$2" suffix="$3" plugin="$4" name="$5" content
  if [[ ! -f "$ROOT/$readme" ]]; then not_ok "$readme exists"; return; fi
  content="$(section "$ROOT/$readme" "$heading" "### $plugin$suffix")"
  if [[ $'\n'"$content" == *$'\n'"| \`$name\` |"* ]]; then
    ok "$readme: lists $plugin/$name"
  else
    not_ok "$readme: lists $plugin/$name"
  fi
}

count=0
for f in "$ROOT"/plugins/*/skills/*/SKILL.md; do
  [[ -f "$f" ]] || continue
  rel="${f#"$ROOT"/plugins/}"; plugin="${rel%%/*}"
  name="$(basename "$(dirname "$f")")"
  count=$((count+1))
  check_readme README.md "## Skills" " plugin" "$plugin" "$name"
  check_readme README.ja.md "## スキル" " プラグイン" "$plugin" "$name"
done
[[ $count -gt 0 ]] || not_ok "skills: found skills to check"

printf '\n# readme-parity: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
