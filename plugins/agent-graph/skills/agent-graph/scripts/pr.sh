#!/usr/bin/env bash
# agent-graph pr — push, create a ready PR, and print its URL.
# Usage: pr.sh --title <title> --body-file <absolute-path> [--gh-user <account>]
#              [--base main] [--draft]
# Resolve authentication once, without switching gh's machine-wide active user.
# Pin the token for gh operations and HTTPS GitHub pushes so another session's
# gh auth switch cannot change the account used partway through this run.
set -uo pipefail

die() { echo "agent-graph pr: $*" >&2; exit 1; }

TITLE=""; BODY_FILE=""; GH_USER=""; BASE="main"; DRAFT=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title|--body-file|--gh-user|--base)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      case "$1" in
        --title) TITLE="$2" ;;
        --body-file) BODY_FILE="$2" ;;
        --gh-user) GH_USER="$2"; [[ -n "$GH_USER" ]] || die "--gh-user requires a nonempty account" ;;
        --base) BASE="$2" ;;
      esac
      shift 2 ;;
    --draft) DRAFT=true; shift ;;
    -h|--help)
      echo "Usage: pr.sh --title <title> --body-file <absolute-path> [--gh-user <account>] [--base main] [--draft]"
      exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$TITLE" ]] || die "--title is required"
[[ -z "$GH_USER" || "$GH_USER" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || die "invalid --gh-user value: $GH_USER"
[[ -n "$BODY_FILE" && -f "$BODY_FILE" ]] || die "--body-file not found: $BODY_FILE"
[[ "$BODY_FILE" == /* ]] || die "--body-file must be an absolute path: $BODY_FILE"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "run inside a git worktree"
[[ -z "$(git status --porcelain)" ]] || die "worktree is not clean; commit changes before running"
BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ -n "$BRANCH" ]] || die "cannot push a detached HEAD"
[[ "$BRANCH" != "$BASE" ]] || die "branch equals base ($BASE); run on a working branch"

# Environment tokens take precedence, as in gh. Without one, obtain the named
# user's token or gh's active token exactly once, then resolve that token's owner.
if [[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]]; then
  TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"
else
  token_args=(auth token)
  [[ -z "$GH_USER" ]] || token_args+=(--user "$GH_USER")
  TOKEN="$(gh "${token_args[@]}" 2>/dev/null)" && [[ -n "$TOKEN" ]] \
    || die "cannot obtain gh token for ${GH_USER:-the active account}; check gh auth login"
fi
LOGIN="$(GH_TOKEN="$TOKEN" gh api user --jq .login 2>/dev/null)" \
  || die "cannot resolve the token's account"
[[ "$LOGIN" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || die "invalid token account: $LOGIN"
if [[ -n "$GH_USER" ]]; then
  [[ "${LOGIN,,}" == "${GH_USER,,}" ]] || die "token belongs to $LOGIN, not --gh-user $GH_USER"
else
  GH_USER="$LOGIN"
fi
ghx() { GH_TOKEN="$TOKEN" gh "$@"; }

# HTTPS GitHub remotes use a token-pinned helper. SSH aliases such as
# gh-<account>: and other remotes use their existing transport authentication.
REMOTE_URL="$(git config --get remote.origin.pushurl 2>/dev/null || git config --get remote.origin.url 2>/dev/null || true)"
if [[ "$REMOTE_URL" == https://github.com/* || "$REMOTE_URL" == https://*@github.com/* ]]; then
  AGENT_GRAPH_PUSH_TOKEN="$TOKEN" GIT_TERMINAL_PROMPT=0 git \
    -c 'credential.https://github.com.helper=' \
    -c "credential.https://github.com.helper=!f() { echo username=$GH_USER; echo \"password=\$AGENT_GRAPH_PUSH_TOKEN\"; }; f" \
    push -u origin HEAD >&2 || die "push failed: $BRANCH"
else
  git push -u origin HEAD >&2 || die "push failed: $BRANCH"
fi

if ! LIST_OUT="$(ghx pr list --head "$BRANCH" --base "$BASE" --state open --json url,isDraft --jq '.[0] | select(. != null) | "\(.url) \(.isDraft)"' 2>&1)"; then
  die "cannot check existing PRs (push succeeded); fix the cause and rerun: $LIST_OUT"
fi
EXISTING="$LIST_OUT"
if [[ -n "$EXISTING" ]]; then
  EXISTING_URL="${EXISTING%% *}"
  echo "PR already exists (push applied): $EXISTING_URL" >&2
  if [[ "${EXISTING##* }" == true ]] && ! $DRAFT; then
    ghx pr ready "$EXISTING_URL" >&2 || die "cannot mark draft PR ready: $EXISTING_URL"
    echo "marked draft PR ready" >&2
  fi
  echo "$EXISTING_URL"
  exit 0
fi

args=(pr create --base "$BASE" --head "$BRANCH" --title "$TITLE" --body-file "$BODY_FILE")
$DRAFT && args+=(--draft)
OUT="$(ghx "${args[@]}" 2>&1)" || die "PR creation failed: $OUT"
URL="$(grep -Eo 'https://[^[:space:]]+/pull/[0-9]+' <<<"$OUT" | tail -1)"
[[ -n "$URL" ]] || die "cannot extract PR URL: $OUT"
echo "$URL"
