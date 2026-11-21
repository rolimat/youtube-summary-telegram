---
name: deploy
description: Use when asked to deploy, release or roll back youtube-summary-telegram, to push config.yml / channels.yml / .env changes to the server, or to check whether the service on the server is healthy.
---

# Deploy and operate youtube-summary-telegram

The service runs as one container on the owner's server. `git push production main`
deploys: the server's `post-receive` hook checks out `main` and runs
`docker compose up -d --build`, so no manual restart is needed after a code push.
Config files are NOT in git; they are copied to the server by hand.

## Setup facts

- Server coordinates: `source scripts/server.env` gives `SERVER_SSH_TARGET` and
  `SERVER_YST_HOME` (untracked file; never write its values into tracked files).
- Owner's private config lives in `local/config/` (git-ignored): `.env`, `config.yml`,
  `channels.yml`. On the server it lives in `$SERVER_YST_HOME/config/`.
- In Claude Code, `ssh` / `scp` / `git push production` need the sandbox disabled
  (they read `~/.ssh`). Use `ssh -o BatchMode=yes` so a missing key fails instead of hanging.
- Every manual `docker compose` command on the server runs in `$SERVER_YST_HOME/app`
  with `export YST_UID=$(id -u) YST_GID=$(id -g)` first, otherwise compose recreates the
  container as uid 1000.

Define this helper once per shell; all recipes below use it:

```bash
source scripts/server.env
srv() { ssh -o BatchMode=yes "$SERVER_SSH_TARGET" "cd '$SERVER_YST_HOME/app' && export YST_UID=\$(id -u) YST_GID=\$(id -g) && docker compose --env-file '$SERVER_YST_HOME/config/.env' $*"; }
```

## Recipes

**Deploy the latest main** (after a PR is merged):

```bash
git switch main && git pull --ff-only origin main
gh run list --branch main --limit 1          # must show "completed success"
git push production main                     # returns after the hook rebuilt and restarted
srv ps && srv logs --tail 30
```

If `gh run list` shows a failed or running CI run, do not deploy. If the `production`
remote is missing: `git remote add production "$SERVER_SSH_TARGET:$SERVER_YST_HOME/repo.git"`.

**Change config** (`config.yml` / `channels.yml` edited in `local/config/`):

```bash
scp local/config/config.yml local/config/channels.yml "$SERVER_SSH_TARGET:$SERVER_YST_HOME/config/"
srv restart
srv ps && srv logs --tail 30     # a bad config shows "exit code 2" and names the file and key
```

A changed `local/config/.env` must keep the server's `YST_HOME`, and needs `up -d`
(`restart` does not re-read env files):

```bash
sed "s|^YST_HOME=.*|YST_HOME=$SERVER_YST_HOME|" local/config/.env | ssh -o BatchMode=yes "$SERVER_SSH_TARGET" "umask 077; cat > '$SERVER_YST_HOME/config/.env'"
srv up -d
```

**Roll back** — read `srv logs --tail 50` first: a config or `.env` error is fixed with
the config recipe, not a rollback. A rollback is a force push to production: ask the owner
before running it. The last good sha is the one deployed before the failing push
(second line of the log below). Afterwards, fix forward on `main` through a PR and deploy
normally; the next `git push production main` needs no `--force` once `main` contains
the good commit.

```bash
git fetch production && git log --oneline -5 production/main   # pick the last good sha
git push production <good-sha>:main --force
srv ps && srv logs --tail 30
```

## Healthy means

- `srv ps` shows the container `running` (not `restarting`).
- The latest log lines include `Iteration done: CycleResult(...)` with
  `auth_failed=False`, or `Bootstrapped state` on a fresh state.
- `Iteration failed` with a traceback, `restarting` in `ps`, or exit code 2 (config error,
  the log names the file and key) means not healthy — read the log before acting.
- `NotebookLM session expired` → use the `renew-notebooklm-session` skill.

## Common mistakes

| Mistake | Effect |
|---|---|
| Pushing a stale local `main` | deploys old code; always `git pull --ff-only` first |
| `docker compose` on the server without the `YST_UID`/`YST_GID` export | container recreated as uid 1000 |
| `restart` after editing `.env` | old env kept; use `up -d` |
| Committing `local/` or `scripts/server.env` to the public repo | leaks private values; both are git-ignored |
| Editing `scripts/ops/post-receive` without re-copying it to `$SERVER_YST_HOME/repo.git/hooks/` | the server keeps the old hook |
