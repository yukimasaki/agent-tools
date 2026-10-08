#!/usr/bin/env bash
# Test pr.sh and review.sh with stubbed gh and Codex; no live service calls.
# Git is real, but every origin is a temporary local bare repository.
#
# Fixture scope:
set -uo pipefail
unset GH_TOKEN GITHUB_TOKEN

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PR="$ROOT/plugins/agent-graph/skills/agent-graph/scripts/pr.sh"
REVIEW="$ROOT/plugins/agent-graph/skills/agent-graph/scripts/review.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-graph-tools.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT

PASS=0; FAIL=0
ok()     { PASS=$((PASS+1)); printf 'ok %s\n' "$1"; }
not_ok() { FAIL=$((FAIL+1)); printf 'not ok %s\n' "$1" >&2; }
expect() { local name="$1"; shift; if "$@"; then ok "$name"; else not_ok "$name"; fi; }

BIN="$TMP/bin"; mkdir -p "$BIN"
REAL_GIT="$(command -v git)"
cat >"$BIN/git" <<STUB
#!/usr/bin/env bash
printf '%s\\n' "\$*" >>"\${STUB_DIR:-/dev/null}/git.log"
if [[ " \$* " == *" push "* ]]; then
  printf '%s\\n' "\${AGENT_GRAPH_PUSH_TOKEN:-}" >>"\$STUB_DIR/push.tokens"
  [[ ! -f "\$STUB_DIR/switch.on.push" ]] || echo other-account >"\$STUB_DIR/active"
fi
exec "$REAL_GIT" "\$@"
STUB
cat >"$BIN/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_DIR/gh.log"
printf '%s\n' "$GH_TOKEN" >>"$STUB_DIR/gh.tokens"
case "$1 $2" in
  "auth switch") echo "gh auth switch must not be used" >>"$STUB_DIR/switch.called"; exit 1 ;;
  "auth token")
    [[ -f "$STUB_DIR/token.fail" ]] && exit 1
    [[ ! -f "$STUB_DIR/token.empty" ]] || exit 0
    if [[ "${3:-}" == --user ]]; then account="$4"; else account="$(cat "$STUB_DIR/active" 2>/dev/null || echo octocat)"; fi
    echo "tok-$account"; exit 0 ;;
  "api user")
    [[ -f "$STUB_DIR/login.fail" ]] && exit 1
    if [[ -f "$STUB_DIR/login" ]]; then cat "$STUB_DIR/login"; elif [[ "$GH_TOKEN" == tok-* ]]; then echo "${GH_TOKEN#tok-}"; else echo octocat; fi
    exit 0 ;;
  "pr list") [[ -f "$STUB_DIR/list.fail" ]] && { echo "boom" >&2; exit 1; }; [[ -f "$STUB_DIR/existing-pr" ]] && cat "$STUB_DIR/existing-pr"; exit 0 ;;
  "pr ready") exit 0 ;;
  "pr create") echo "https://github.com/o/r/pull/42"; exit 0 ;;
esac
STUB
cat >"$BIN/codex" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_DIR/codex.log"
[[ -f "$STUB_DIR/codex.fail" ]] && exit 1
while [[ $# -gt 0 ]]; do [[ "$1" == -o ]] && { echo "Review text" >"$2"; }; shift; done
exit 0
STUB
chmod +x "$BIN/gh" "$BIN/codex" "$BIN/git"

# $1=case name. Create a committed main, a local bare origin, and feat/x.
new_repo() {
  STUB_DIR="$TMP/$1.stub"; mkdir -p "$STUB_DIR"
  REPO="$TMP/$1.repo"
  git init -q --bare "$TMP/$1.origin"
  git init -q -b main "$REPO"
  git -C "$REPO" config user.name t; git -C "$REPO" config user.email t@example.com
  git -C "$REPO" remote add origin "$TMP/$1.origin"
  echo a >"$REPO/a"; git -C "$REPO" add a; git -C "$REPO" commit -qm base
  git -C "$REPO" push -q origin main
  git -C "$REPO" checkout -q -b feat/x
  echo b >"$REPO/b"; git -C "$REPO" add b; git -C "$REPO" commit -qm work
  printf 'Body\n' >"$TMP/$1.body"
  BODY="$TMP/$1.body"
}
run() { (cd "$REPO" && STUB_DIR="$STUB_DIR" PATH="$BIN:$PATH" AGENT_GRAPH_STATE_ROOT="$TMP/state" "$@"); }

# ---------- pr.sh ----------

new_repo pr1
out="$(run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat 2>/dev/null)"; rc=$?
expect "pr.sh prints the PR URL" test "$rc" -eq 0 -a "$out" = "https://github.com/o/r/pull/42"
expect "Pushes the branch" bash -c 'git -C "$1" ls-remote --exit-code --heads "$2" feat/x >/dev/null' _ "$REPO" "$TMP/pr1.origin"
expect "Creates a ready PR without --draft" bash -c '! grep -q -- "--draft" "$1"' _ "$STUB_DIR/gh.log"
expect "Searches existing PRs with --base" grep -q -- "pr list --head feat/x --base main --state open" "$STUB_DIR/gh.log"
expect "Passes base, title, and body to gh pr create" grep -q -- "pr create --base main --head feat/x --title Title --body-file $BODY" "$STUB_DIR/gh.log"
expect "Does not use gh auth switch" test ! -e "$STUB_DIR/switch.called"
expect "Pins --gh-user token in GH_TOKEN for each operation" bash -c 'grep -q "^tok-octocat$" "$1" && ! grep -qv "^tok-octocat$" <(grep -v "^$" "$1")' _ "$STUB_DIR/gh.tokens"
expect "Does not add a credential helper for local remotes" bash -c '! grep -q "credential.https" "$1"' _ "$STUB_DIR/git.log"

new_repo pr1b
git -C "$REPO" config remote.origin.url https://github.com/o/r.git
git -C "$REPO" config url."$TMP/pr1b.origin".insteadOf https://github.com/o/r.git
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Pushes HTTPS GitHub remotes with a credential helper" bash -c '[[ "$1" -eq 0 ]] && grep -q "credential.https://github.com.helper= " "$2" && grep -q "credential.https://github.com.helper=!f()" "$2"' _ "$rc" "$STUB_DIR/git.log"
helper="$(grep -o '!f() {.*; f' "$STUB_DIR/git.log" | head -1)"
expect "The helper returns the named account token" bash -c 'out="$(AGENT_GRAPH_PUSH_TOKEN=tok-octocat sh -c "${1#!}")"; [[ "$out" == *username=octocat* && "$out" == *password=tok-octocat* ]]' _ "$helper"
expect "The branch was pushed" bash -c 'git -C "$1" ls-remote --exit-code --heads "$2" feat/x >/dev/null' _ "$REPO" "$TMP/pr1b.origin"

new_repo pr2
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat --draft >/dev/null 2>&1
expect "--draft creates a draft" grep -q -- "pr create .*--draft" "$STUB_DIR/gh.log"

new_repo pr3
echo dirty >"$REPO/c"
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Rejects a dirty worktree" test "$rc" -ne 0
expect "Does not push or create a PR after rejecting the worktree" bash -c '! git -C "$1" ls-remote --exit-code --heads "$2" feat/x >/dev/null 2>&1 && [[ ! -s "$3/gh.log" ]]' _ "$REPO" "$TMP/pr3.origin" "$STUB_DIR"

new_repo pr4
echo "https://github.com/o/r/pull/7 false" >"$STUB_DIR/existing-pr"
out="$(run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat 2>/dev/null)"
expect "Reuses the URL of an existing open PR" test "$out" = "https://github.com/o/r/pull/7"
expect "Does not call pr create for an existing PR" bash -c '! grep -q "pr create" "$1"' _ "$STUB_DIR/gh.log"

new_repo pr4b
echo "https://github.com/o/r/pull/8 true" >"$STUB_DIR/existing-pr"
out="$(run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat 2>/dev/null)"
expect "Marks an existing draft ready before printing the URL" bash -c 'grep -q "pr ready https://github.com/o/r/pull/8" "$1" && [[ "$2" == "https://github.com/o/r/pull/8" ]]' _ "$STUB_DIR/gh.log" "$out"
new_repo pr4c
echo "https://github.com/o/r/pull/9 true" >"$STUB_DIR/existing-pr"
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat --draft >/dev/null 2>&1
expect "Keeps an existing draft when --draft is requested" bash -c '! grep -q "pr ready" "$1"' _ "$STUB_DIR/gh.log"

new_repo pr5
touch "$STUB_DIR/token.fail"
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Stops if token retrieval fails" test "$rc" -ne 0
expect "Does not push when token retrieval fails" bash -c '! git -C "$1" ls-remote --exit-code --heads "$2" feat/x >/dev/null 2>&1' _ "$REPO" "$TMP/pr5.origin"
(cd "$REPO" && STUB_DIR="$STUB_DIR" PATH="$BIN:$PATH" GH_TOKEN=dummy bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1); rc=$?
expect "Uses matching GH_TOKEN without gh auth token" test "$rc" -eq 0

new_repo pr7
echo someone-else >"$STUB_DIR/login"
(cd "$REPO" && STUB_DIR="$STUB_DIR" PATH="$BIN:$PATH" GH_TOKEN=dummy bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1); rc=$?
expect "Stops when token owner differs from --gh-user" test "$rc" -ne 0
expect "Does not push or create a PR on owner mismatch" bash -c '! git -C "$1" ls-remote --exit-code --heads "$2" feat/x >/dev/null 2>&1 && ! grep -q "pr create" "$3"' _ "$REPO" "$TMP/pr7.origin" "$STUB_DIR/gh.log"

new_repo pr5b
touch "$STUB_DIR/list.fail"
run bash "$PR" --title "Title" --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Stops on gh pr list failure without creating a PR" bash -c '[[ "$1" -ne 0 ]] && ! grep -q "pr create" "$2"' _ "$rc" "$STUB_DIR/gh.log"

new_repo pr6
out="$(run bash "$PR" --title t --body-file "$BODY" 2>/dev/null)"; rc=$?
expect "Resolves the active account when --gh-user is omitted" test "$rc" -eq 0 -a "$out" = "https://github.com/o/r/pull/42"
expect "Retrieves the active token and resolves its owner once" bash -c '[[ "$(grep -c "^auth token$" "$1")" == 1 && "$(grep -c "^api user " "$1")" == 1 ]]' _ "$STUB_DIR/gh.log"
expect "Pins the resolved active token for PR operations" bash -c '[[ "$(grep -c "^tok-octocat$" "$1")" == 3 ]]' _ "$STUB_DIR/gh.tokens"
expect "Rejects a relative --body-file" bash -c '! (cd "$1" && PATH="$2:$PATH" STUB_DIR="$3" bash "$4" --title t --body-file rel.md --gh-user u) >/dev/null 2>&1' _ "$REPO" "$BIN" "$STUB_DIR" "$PR"

new_repo empty-explicit-user
run bash "$PR" --title t --body-file "$BODY" --gh-user "" >/dev/null 2>&1; rc=$?
expect "Rejects an explicitly empty --gh-user instead of treating it as omitted" bash -c '[[ "$1" -ne 0 && ! -e "$2/gh.log" && ! -e "$2/git.log" ]]' _ "$rc" "$STUB_DIR"

# Omitted account with environment tokens, including gh's precedence rules.
for token_var in GH_TOKEN GITHUB_TOKEN; do
  new_repo "env-$token_var"
  echo env-owner >"$STUB_DIR/login"
  git -C "$REPO" config remote.origin.url https://github.com/o/r.git
  git -C "$REPO" config url."$TMP/env-$token_var.origin".insteadOf https://github.com/o/r.git
  out="$(run env "$token_var=env-token" bash "$PR" --title t --body-file "$BODY" 2>/dev/null)"; rc=$?
  helper="$(grep -o '!f() {.*; f' "$STUB_DIR/git.log" | head -1)"
  expect "Uses $token_var owner when --gh-user is omitted" test "$rc" -eq 0 -a "$out" = "https://github.com/o/r/pull/42"
  expect "Does not retrieve a stored token with $token_var" bash -c '! grep -q "^auth token" "$1"' _ "$STUB_DIR/gh.log"
  expect "Pins $token_var for all API and PR calls" bash -c '[[ "$(grep -c "^env-token$" "$1")" == 3 ]] && ! grep -qv "^env-token$" "$1"' _ "$STUB_DIR/gh.tokens"
  expect "Pins $token_var for push with the resolved owner" bash -c 'out="$(AGENT_GRAPH_PUSH_TOKEN=env-token sh -c "${1#!}")"; [[ "$out" == *username=env-owner* && "$out" == *password=env-token* ]] && grep -qx env-token "$2"' _ "$helper" "$STUB_DIR/push.tokens"
done

new_repo env-precedence
run env GH_TOKEN=primary GITHUB_TOKEN=secondary bash "$PR" --title t --body-file "$BODY" >/dev/null 2>&1; rc=$?
expect "GH_TOKEN takes precedence over GITHUB_TOKEN" bash -c '[[ "$1" -eq 0 ]] && ! grep -qv "^primary$" "$2"' _ "$rc" "$STUB_DIR/gh.tokens"

# The selected token survives an active-account change during push.
new_repo active-switch
echo octocat >"$STUB_DIR/active"; touch "$STUB_DIR/switch.on.push"
git -C "$REPO" config remote.origin.url https://github.com/o/r.git
git -C "$REPO" config url."$TMP/active-switch.origin".insteadOf https://github.com/o/r.git
run bash "$PR" --title t --body-file "$BODY" >/dev/null 2>&1; rc=$?
expect "Active account changes during push without changing PR credentials" bash -c '[[ "$1" -eq 0 && "$(cat "$2/active")" == other-account ]] && ! grep -qv "^tok-octocat$" <(grep -v "^$" "$2/gh.tokens") && grep -qx tok-octocat "$2/push.tokens"' _ "$rc" "$STUB_DIR"
expect "Account switch does not trigger another token lookup or owner resolution" bash -c '[[ "$(grep -c "^auth token$" "$1")" == 1 && "$(grep -c "^api user " "$1")" == 1 ]]' _ "$STUB_DIR/gh.log"

for failure in token.fail token.empty login.fail; do
  new_repo "active-$failure"
  touch "$STUB_DIR/$failure"
  run bash "$PR" --title t --body-file "$BODY" >/dev/null 2>&1; rc=$?
  expect "Omitted account stops on $failure without push or PR creation" bash -c '[[ "$1" -ne 0 ]] && ! grep -q "push -u" "$2/git.log" && ! grep -q "^pr create" "$2/gh.log"' _ "$rc" "$STUB_DIR"
done

new_repo stored-owner-mismatch
echo other-account >"$STUB_DIR/login"
run bash "$PR" --title t --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Rejects an explicit user differing from the stored token owner before push" bash -c '[[ "$1" -ne 0 ]] && ! grep -q "push -u" "$2/git.log" && ! grep -q "^pr create" "$2/gh.log"' _ "$rc" "$STUB_DIR"

new_repo case-insensitive-owner
echo OctoCat >"$STUB_DIR/login"
run env GH_TOKEN=dummy bash "$PR" --title t --body-file "$BODY" --gh-user octocat >/dev/null 2>&1; rc=$?
expect "Explicit user comparison is case insensitive" test "$rc" -eq 0

new_repo env-owner-failure
touch "$STUB_DIR/login.fail"
run env GH_TOKEN=dummy bash "$PR" --title t --body-file "$BODY" >/dev/null 2>&1; rc=$?
expect "Stops before push when the environment token owner cannot be resolved" bash -c '[[ "$1" -ne 0 ]] && ! grep -q "push -u" "$2/git.log"' _ "$rc" "$STUB_DIR"

new_repo invalid-owner
printf 'invalid account\n' >"$STUB_DIR/login"
run env GH_TOKEN=dummy bash "$PR" --title t --body-file "$BODY" >/dev/null 2>&1; rc=$?
expect "Rejects invalid resolved accounts before inserting them into a helper" bash -c '[[ "$1" -ne 0 ]] && ! grep -q "push -u" "$2/git.log"' _ "$rc" "$STUB_DIR"

# ---------- review.sh ----------

new_repo rv1
git -C "$REPO" checkout -q main; echo m >"$REPO/m"; git -C "$REPO" add m; git -C "$REPO" commit -qm "main advanced"; git -C "$REPO" push -q origin main
git -C "$REPO" checkout -q feat/x
EXPECT_BASE="$(git -C "$REPO" merge-base "$(git -C "$REPO" rev-parse origin/main~1)" HEAD)"
out="$(run bash "$REVIEW" 2>/dev/null)"; rc=$?
expect "review.sh prints only the output file path" bash -c '[[ "$1" -eq 0 && -f "$2" && "$2" == "$3"/state/reviews/feat-x-*.md ]]' _ "$rc" "$out" "$TMP"
expect "Passes merge-base SHA to --base" grep -q -- "exec review --base $EXPECT_BASE -o $out" "$STUB_DIR/codex.log"
expect "Uses merge-base instead of the main tip" bash -c '[[ "$1" != "$(git -C "$2" rev-parse origin/main)" ]]' _ "$EXPECT_BASE" "$REPO"
expect "Does not pass a criteria prompt" bash -c '[[ "$(wc -l <"$1")" -eq 1 && "$(cat "$1")" == "exec review --base "* ]]' _ "$STUB_DIR/codex.log"

new_repo rv2
out="$(run bash "$REVIEW" --uncommitted --out "$TMP/out/r.md" 2>/dev/null)"
expect "--uncommitted does not use --base" grep -q -- "exec review --uncommitted -o $TMP/out/r.md" "$STUB_DIR/codex.log"
expect "Uses the --out destination" test "$out" = "$TMP/out/r.md"

new_repo rv4
git -C "$REPO" checkout -q -b develop main; echo d >"$REPO/d"; git -C "$REPO" add d; git -C "$REPO" commit -qm dev; git -C "$REPO" push -q origin develop
git -C "$REPO" checkout -q -b feat/y; echo y >"$REPO/y"; git -C "$REPO" add y; git -C "$REPO" commit -qm y
DEV_SHA="$(git -C "$REPO" rev-parse develop)"
out="$(run bash "$REVIEW" --base develop 2>/dev/null)"
expect "--base resolves merge-base against the requested ref" grep -q -- "exec review --base $DEV_SHA -o" "$STUB_DIR/codex.log"

new_repo rv5
mkdir -p "$TMP/old" "$TMP/silent"; echo "Previous output" >"$TMP/old/r.md"
printf '#!/usr/bin/env bash\nexit 0\n' >"$TMP/silent/codex"; chmod +x "$TMP/silent/codex"
(cd "$REPO" && STUB_DIR="$STUB_DIR" PATH="$TMP/silent:$BIN:$PATH" bash "$REVIEW" --uncommitted --out "$TMP/old/r.md" >/dev/null 2>&1); rc=$?
expect "Does not return stale output when Codex exits zero without writing" test "$rc" -ne 0
expect "Removes existing output before review" test ! -e "$TMP/old/r.md"

new_repo rv6
run bash "$REVIEW" --uncommitted --base develop >/dev/null 2>&1; rc=$?
expect "Rejects --uncommitted combined with --base" bash -c '[[ "$1" -ne 0 ]] && [[ ! -e "$2/codex.log" ]]' _ "$rc" "$STUB_DIR"

new_repo rv3
touch "$STUB_DIR/codex.fail"
run bash "$REVIEW" >/dev/null 2>&1; rc=$?
expect "Fails when Codex fails" test "$rc" -ne 0

printf '\n# agent-graph-tools: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
