# youtube-summary-telegram

Polls YouTube channel RSS feeds, asks NotebookLM to summarise each new video with your own prompt,
and posts the result to a Telegram group. One Docker container, deployed with `git push`.

Sample message (Telegram HTML):

```
🎬 <b>Example Channel</b> — Video title

Summary text returned by NotebookLM.

• First key point
• Second key point

🔗 Watch video
```

## How it works

Every `poll_interval_minutes` the service runs one iteration:

- **Keepalive**: `notebooks.list()` on NotebookLM, day and night, to keep the session alive.
- **Active hours**: videos are summarised and sent only inside `active_hours`. The expired-session alert
  is also sent only inside them, once.
- **Bootstrap**: on the first run, existing feed entries are marked seen and nothing is sent. If any feed
  fails during bootstrap nothing is saved and it is retried next iteration (fix or remove the channel).
- **Dedup**: a video is processed once. Videos older than `max_video_age_hours` or not matching the
  channel's `title_filter` are ignored.
- **Summarise**: each video gets its own NotebookLM conversation; the notebook's sources are replaced
  by the video. A video NotebookLM cannot process yet is deferred to the next iteration.
- **Send**: the message is stored in `seen.json` first, so a failed send is retried (up to 3 sends)
  without calling NotebookLM again. A Telegram rejection stops that iteration's deliveries.
- **Give up**: after 3 failed summarise attempts a video is dropped and logged as `Given up`.
- **Telegram**: `parse_mode: HTML`. Long messages are split into chunks: header on the first,
  link on the last.

## Requirements

- Docker with Compose (and [uv](https://docs.astral.sh/uv/) with Python 3.15 for local runs/development).
- A dedicated Telegram bot ([BotFather](https://t.me/BotFather)) added to the target group.
- A Google account with [NotebookLM](https://notebooklm.google.com).
- For deploy: a Linux server with Docker, git and `flock` (util-linux), reachable over SSH.
- For the session sync: `notebooklm` CLI (from `notebooklm-py`), `jq`, `rsync`, `ssh` on your desktop.

## Setup

1. **Notebook.** Create a dedicated NotebookLM notebook. Its id is the last URL path segment.
2. **Session.** `cp scripts/server.env.sample scripts/server.env`, fill it, then run:

   ```bash
   scripts/notebooklm_session/sync_session.sh --force
   ```

   It logs in with the `youtube-summary` profile (override with `NOTEBOOKLM_PROFILE`) and copies the
   session to `<YST_HOME>/secrets/notebooklm/storage_state.json` on the server.
3. **Channel id.** Look for `channel_id` in the channel page source, or open
   `https://www.youtube.com/@handle` → "Share channel" → "Copy channel ID" (`UC...`).
4. **Chat id.** Add the bot to the group, send a message, then open
   `https://api.telegram.org/bot<bot-token>/getUpdates` and read `message.chat.id`
   (negative for groups).

## Configuration

Config files live in `<YST_HOME>/config/`. Changing `config.yml` or `channels.yml` needs
`docker compose restart`. Copy the `*.sample` files in the repo as a starting point.

### `.env`

| Key | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | Token of this service's own bot, e.g. `<bot-token>` |
| `TELEGRAM_CHAT_ID` | yes | Target chat, e.g. `<chat-id>` |
| `YST_HOME` | Docker | Absolute directory holding `repo.git/ app/ config/ state/ secrets/` |
| `NOTEBOOKLM_STORAGE_STATE_PATH` | no Docker | Path to `storage_state.json` (Compose sets it) |
| `CONFIG_DIR` | no Docker | Dir with `config.yml` and `channels.yml` (default `/config`) |
| `STATE_DIR` | no Docker | Dir for `seen.json` (default `/state`) |

### `config.yml`

| Key | Type | Description |
| --- | --- | --- |
| `notebooklm.notebook_id` | string | Notebook id, e.g. `"<notebook-id>"` |
| `poll_interval_minutes` | int > 0 | Iteration period, e.g. `15` |
| `active_hours` | `{start, end, timezone}` | Hours 0-23 (inclusive, `start <= end`) and an IANA zone, e.g. `{start: 7, end: 23, timezone: Europe/Madrid}` |
| `max_video_age_hours` | int > 0 | Ignore older videos, e.g. `48` |
| `language` | `en` \| `es` | Default `en`. Language of the fixed texts (link text, untitled title, auth alert). The summary language comes from your prompt |
| `prompt` | string | Your prompt. No prompt ships with the repo; it must make NotebookLM answer with ONLY `{"summary": str, "key_points": [str]}` |

### `channels.yml`

| Key | Type | Description |
| --- | --- | --- |
| `channels[].name` | string | Shown in the message header |
| `channels[].channel_id` | string | `UC...` id; must be unique |
| `channels[].title_filter` | list of strings | Optional, default none. Only titles containing one of them |

### `scripts/server.env`

| Key | Description |
| --- | --- |
| `SERVER_SSH_TARGET` | `<user>@<host>` |
| `SERVER_YST_HOME` | Absolute `<YST_HOME>` on the server |

## Run locally

With uv (needs `NOTEBOOKLM_STORAGE_STATE_PATH`, `CONFIG_DIR`, `STATE_DIR` and the Telegram variables in the environment):

```bash
uv sync
uv run youtube-summary --once   # one iteration, then exit
uv run youtube-summary          # loop
```

With Docker (`<YST_HOME>` laid out as in the next section, `YST_HOME` set in its `config/.env`):

```bash
docker compose --env-file <YST_HOME>/config/.env up -d --build
```

The container runs as `YST_UID:YST_GID` (default 1000:1000); export them if your ids differ.

## Deploy to your own server

Layout, owned by the server user (uid `YST_UID`, default 1000):

```
<YST_HOME>/
  repo.git/            bare repo with hooks/post-receive
  app/                 checked-out code (created by the hook)
  config/              .env, config.yml, channels.yml
  state/               seen.json (created by the hook)
  secrets/notebooklm/  storage_state.json (created by the hook)
```

```bash
ssh <ssh-target> "git init --bare <YST_HOME>/repo.git"
scp scripts/ops/post-receive <ssh-target>:<YST_HOME>/repo.git/hooks/post-receive
ssh <ssh-target> "chmod +x <YST_HOME>/repo.git/hooks/post-receive"
# put .env, config.yml and channels.yml in <YST_HOME>/config/, then:
git remote add production <ssh-target>:<YST_HOME>/repo.git
git push production main
```

The hook derives `YST_HOME` from its own location, creates `app/`, `state/` and `secrets/notebooklm`,
checks out `main`, runs `docker compose up -d --build` as the deploy user's uid/gid, serialises deploys
with `flock` and prunes only this project's dangling images. Pushing other refs does not deploy.
Re-run it by hand with `POST_RECEIVE_FORCE=1 <YST_HOME>/repo.git/hooks/post-receive`.

Rollback:

```bash
git push production <sha>:main --force
```

### Private copy

GitHub does not allow a private fork of a public repo. Keep a separate private repo with this one
as `upstream` and put your real config and prompt there.

## Renewing the NotebookLM session

When the log shows `NotebookLM session expired` (and the group gets the alert):

```bash
scripts/notebooklm_session/sync_session.sh --force
```

No restart is needed; the next iteration picks it up. Without `--force` it logs in again only if
the stored session fails `notebooklm auth check`.

## Operations

Run these in `<YST_HOME>/app` on the server:

```bash
docker compose --env-file <YST_HOME>/config/.env logs --tail 50 -f
docker compose --env-file <YST_HOME>/config/.env restart   # after editing config.yml / channels.yml
```

- **Reset state**: delete `<YST_HOME>/state/seen.json` (no `sudo` needed); the next iteration
  bootstraps again and sends nothing for existing videos.
- **Add a channel**: edit `channels.yml`, then restart.
- **Log lines**: `Given up summarising ...` / `Given up sending ...` means the video was dropped after
  3 failures and will not be retried. `Deferred, not processable yet` is retried next iteration.

## Dependency updates

Dependabot runs weekly with a 3-day cooldown. Auto-merged (once CI passes): Python patch updates,
GitHub Actions and pre-commit minor and patch updates, Docker digest-only updates. Manual: `notebooklm-py`,
Python minors, Docker tag changes and all majors. For `notebooklm-py`, run before merging:

```bash
uv run python scripts/smoke_notebooklm.py <video-url> [--prompt-file prompt.txt]
```

(needs `NOTEBOOKLM_STORAGE_STATE_PATH` and `CONFIG_DIR` set.)

Repo owner settings required: "Allow auto-merge", and a ruleset on `main` requiring the `test` and
`image` checks (branches up to date), with admin bypass.

## Troubleshooting

- **Auth alert / `session expired`**: renew the session (above).
- **`Deferred, not processable yet`**: YouTube video not ready for NotebookLM (e.g. live or premiere);
  retried automatically.
- **Telegram 400**: the HTML was rejected (see the logged body). The message is retried up to 3 times,
  then `Given up sending`.
- **Bootstrap aborted, feed failed**: a feed is unreachable or the `channel_id` is wrong; fix or remove it.
- **Keepalive fails right after renewing the session**: renewing while a NotebookLM call is in flight
  can let the running container write back older cookies. Sync again.
- **Exit code 2**: invalid config; the message names the file and key.

## Development

```bash
uv sync
uv run pre-commit run --all-files
uv run pytest -q --cov=youtube_summary --cov-fail-under=90
uv run mypy src tests
```

TDD, `mypy --strict`, all external APIs mocked (no network in tests). CI (`.github/workflows/ci.yml`)
runs the `test` job (pre-commit + pytest with 90% coverage) and the `image` job (Docker build).

## License

Apache 2.0, see [LICENSE](LICENSE).
