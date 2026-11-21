---
name: renew-notebooklm-session
description: Use when the Telegram group gets the "NotebookLM session expired" alert, the service logs "NotebookLM session expired" or auth_failed=True, the NotebookLM session must be copied to the server, or the notebooklm CLI / Playwright is missing on the desktop.
---

# Renew the NotebookLM session

The service reads `$SERVER_YST_HOME/secrets/notebooklm/storage_state.json` on every
NotebookLM call, so copying a fresh file there is enough: no container restart.
The desktop session lives in the NotebookLM profile `youtube-summary`
(`~/.notebooklm/profiles/youtube-summary/storage_state.json`), logged in with the
service's dedicated Google account.

## Prerequisites (once per Mac)

- `scripts/server.env` with `SERVER_SSH_TARGET` and `SERVER_YST_HOME` (untracked; on a
  fresh Mac copy it from the owner's private repo).
- SSH key access to the server (`ssh -o BatchMode=yes "$SERVER_SSH_TARGET" true` succeeds).
- The CLI with browser support, outside the project venv, plus Chromium:

```bash
uv tool install "notebooklm-py[browser]==0.8.6"
"$(uv tool dir)/notebooklm-py/bin/playwright" install chromium
```

`jq` and `rsync` must be on PATH. In Claude Code, run the sync and ssh commands with the
sandbox disabled (they read `~/.ssh`).

## Renew

```bash
scripts/notebooklm_session/sync_session.sh --force
```

A Chromium window opens: log in with the service's Google account and wait for the
NotebookLM home page; the CLI detects the login itself (up to 5 minutes, nothing to type in
the terminal). The script then checks the session and rsyncs it to the server.
Without `--force` it only logs in again when `notebooklm auth check` fails, then syncs.

If Chromium crashes on macOS, log in with Chrome and sync without forcing:

```bash
NOTEBOOKLM_PROFILE=youtube-summary notebooklm login --browser chrome
scripts/notebooklm_session/sync_session.sh
```

## Prove the server uses it

```bash
source scripts/server.env
shasum -a 256 ~/.notebooklm/profiles/youtube-summary/storage_state.json
ssh -o BatchMode=yes "$SERVER_SSH_TARGET" "sha256sum '$SERVER_YST_HOME/secrets/notebooklm/storage_state.json'"
```

Compare within a minute of the sync: the hashes match then. Later they diverge because the
container rotates the cookies and rewrites the file, which is expected and not a failure.
Then wait for the next iteration (every `poll_interval_minutes`, 15 by default; container
log timestamps are UTC) and check:

```bash
ssh -o BatchMode=yes "$SERVER_SSH_TARGET" "cd '$SERVER_YST_HOME/app' && docker compose --env-file '$SERVER_YST_HOME/config/.env' logs --since 20m" | grep -E "Iteration done|session expired"
```

Success: an `Iteration done: CycleResult(...)` line timestamped after the sync with
`auth_failed=False`. No line yet means the iteration has not run: check again after one
more interval. The group alert is sent once per outage and re-arms after the first
successful keepalive.

## If it still fails

| Symptom | Action |
|---|---|
| `notebooklm auth check` fails right after login | logged into the wrong Google account; repeat the login with the service account |
| hashes differ right after the sync | the sync did not reach the server; re-run and read the rsync error |
| `session expired` again right after a good sync | a NotebookLM call in flight wrote older cookies back; sync once more |
| `Missing required tool: notebooklm` / `Playwright not installed` | run the Prerequisites install block |
| `Missing .../server.env` | create `scripts/server.env` (see Prerequisites) |
