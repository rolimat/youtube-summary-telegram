---
name: deploy
description: Deploy youtube-summary-telegram to the server by pushing main to the production remote, then verify the logs. Use when asked to deploy, release or roll back the service.
---

# Deploy

Read the real server values from `scripts/server.env` (untracked; `SERVER_SSH_TARGET`,
`SERVER_YST_HOME`). Never write them into the repo. Remote commands use double quotes so
the variables expand locally.

Deploy only after CI is green on main.

The `post-receive` hook is installed by copying `scripts/ops/post-receive` to
`<YST_HOME>/repo.git/hooks/post-receive` on the server (re-install it after editing it).

1. Run validation: `uv run pre-commit run --all-files && uv run pytest -q --cov=youtube_summary --cov-fail-under=90`.
2. `git push origin main`, then wait for CI to pass.
3. `git push production main`.
4. `source scripts/server.env && ssh "$SERVER_SSH_TARGET" "cd '$SERVER_YST_HOME/app' && docker compose --env-file '$SERVER_YST_HOME/config/.env' logs --tail 50"`
5. Confirm an iteration log line with a keepalive and no auth error.
6. Rollback: `git push production <previous-sha>:main --force`.
