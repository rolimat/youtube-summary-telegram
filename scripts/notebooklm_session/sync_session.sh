#!/usr/bin/env bash
# Copies the desktop NotebookLM session to the server. Pass --force to log in again first.
# Paths with spaces are not supported.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$here/../server.env"
[ -f "$env_file" ] || { echo "Missing $env_file (copy server.env.sample)" >&2; exit 1; }
# shellcheck source=/dev/null
source "$env_file"
: "${SERVER_SSH_TARGET:?set SERVER_SSH_TARGET in server.env}"
: "${SERVER_YST_HOME:?set SERVER_YST_HOME in server.env}"

case "$SERVER_YST_HOME" in
  *[[:space:]]*) echo "SERVER_YST_HOME must not contain whitespace" >&2; exit 1 ;;
  /*) ;;
  *) echo "SERVER_YST_HOME must be an absolute path (start with /)" >&2; exit 1 ;;
esac

export NOTEBOOKLM_PROFILE="${NOTEBOOKLM_PROFILE:-youtube-summary}"

for tool in notebooklm jq rsync; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

auth_ok() { notebooklm auth check --test --json | jq -e '.status == "ok"' >/dev/null; }

if [ "${1:-}" = "--force" ] || ! auth_ok; then
  notebooklm login
  auth_ok || { echo "NotebookLM login check still failing" >&2; exit 1; }
fi

remote_dir="$SERVER_YST_HOME/secrets/notebooklm"
ssh "$SERVER_SSH_TARGET" "install -d -m 700 '$remote_dir'"
rsync --chmod=u=rw,go-rwx \
  "${NOTEBOOKLM_HOME:-$HOME/.notebooklm}/profiles/$NOTEBOOKLM_PROFILE/storage_state.json" \
  "$SERVER_SSH_TARGET:$remote_dir/storage_state.json"
