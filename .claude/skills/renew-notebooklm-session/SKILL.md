---
name: renew-notebooklm-session
description: Renew the NotebookLM login and copy the session to the server. Use when the service logs a NotebookLM authentication error or the session expired.
---

# Renew the NotebookLM session

Server coordinates live in `scripts/server.env` (untracked). Never write real values into
the repo.

1. Run `scripts/notebooklm_session/sync_session.sh --force` (opens a browser login, then copies the session).
2. No restart is needed: the next iteration picks the new session up.
3. Check the logs for a successful keepalive and no auth error:
   `source scripts/server.env && ssh "$SERVER_SSH_TARGET" "cd '$SERVER_YST_HOME/app' && docker compose --env-file '$SERVER_YST_HOME/config/.env' logs --tail 50"`
