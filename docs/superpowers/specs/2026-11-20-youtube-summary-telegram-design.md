# youtube-summary-telegram — design

Date: 2026-11-20 (revised 2026-11-21)
Repo: https://github.com/rolimat/youtube-summary-telegram (public)
Plan: [`docs/superpowers/plans/2026-11-20-youtube-summary-telegram.md`](../plans/2026-11-20-youtube-summary-telegram.md)

This document is self-contained. The builder has no access to the system this
service replaces; everything reused from it is reproduced in the appendix.

## Intent

A tiny standalone service that watches a configured list of YouTube channels, has
Google NotebookLM summarise each new video, and posts the summary to a Telegram
group. It replaces the YouTube analyzer of the owner's private trading system
("the previous system"), which stored signals in Postgres, resolved tickers and built
weekly summaries. The owner only wants a readable summary per video in Telegram.

## Goals

- Channels in `channels.yml`, each with an optional topic filter on the video title.
- Each new video → NotebookLM → one Telegram message (summary + key points + link).
- Posts to the same Telegram group the previous system used (configured, not hardcoded).
- A dedicated NotebookLM notebook, id in `config.yml`.
- The NotebookLM session is kept alive by a cheap periodic call, day and night.
- NotebookLM usage is limited: a video is summarised at most once when Telegram
  fails, and a failing video stops costing NotebookLM calls after a few attempts.
- Runs as one Docker container on the owner's home Linux server, deployed by
  `git push` over SSH to a bare repo whose `post-receive` hook runs
  `docker compose up -d --build`.
- **Fully independent of the previous system.** Own directory tree on the server, own
  NotebookLM session file and desktop profile, own Telegram bot, own deploy hook.
  The previous system can be deleted from the server tomorrow and this service keeps
  working unchanged.
- Dependencies are kept current by Dependabot, with safe updates auto-merged.
- Public repo: explanatory `README.md`, `CLAUDE.md`, Claude Code skills, GitHub Actions CI.

## Non-goals

- No database, no stored summaries beyond unsent messages, no UI, no HTTP API.
- No ticker resolution or financial signal extraction.
- No weekly or aggregated summaries.

## Anti-goals

- Any credential, cookie, chat id, notebook id, hostname, username, server path or the
  name of the previous system committed to the public repo. Samples use placeholders.
- Any runtime dependency on the previous system: its files, directories, session file,
  bot, containers or network.
- A video posted twice to the group.
- A first run that floods the group with every channel's back catalogue.
- A state file that grows without bound.
- The bot token in logs.

## Approaches (decided: A)

- **A. Long-running container with an internal loop (chosen).** One Python process runs
  an iteration every `poll_interval_minutes`, day and night: it always keeps the
  NotebookLM session alive and only summarises and sends inside `active_hours`.
  `restart: unless-stopped`. Deploy is `git push` + hook.
- B. One-shot container fired by a host systemd timer/cron. No resident process, but
  the timer lives outside the deploy hook and failures are less visible. Rejected.
- C. Celery + Redis. Far too much for an hourly poll. Rejected.

## Runtime

- Python 3.15 (latest stable), uv, base image `ghcr.io/astral-sh/uv:python3.15-trixie-slim`
  pinned by digest (Dependabot bumps the digest).
- The container runs as a non-root user (`user: "${YST_UID:-1000}:${YST_GID:-1000}"`,
  `HOME=/tmp`), so every file it writes on the host belongs to the server user.
- Compose project name is fixed (`name: youtube-summary-telegram`), so it never collides
  with another compose project whose directory is also called `app`.
- SIGTERM/SIGINT: the process finishes the current step, never starts a new video or
  send, and exits. `stop_grace_period: 2m`. A SIGKILL during a NotebookLM call is
  harmless: nothing was sent and the state was saved before the call.
- Logging: `httpx` and `httpcore` loggers at WARNING, because httpx logs request URLs at
  INFO and the Telegram URL contains the bot token.

## Configuration

| File | In git | Content |
|---|---|---|
| `.env` | no — `.env.sample` committed | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `YST_HOME`, optional `YST_UID`/`YST_GID`; `NOTEBOOKLM_STORAGE_STATE_PATH`, `CONFIG_DIR`, `STATE_DIR` only when running without Docker |
| `config.yml` | no — `config.yml.sample` committed | `notebooklm.notebook_id`, `poll_interval_minutes`, `active_hours.{start,end,timezone}`, `max_video_age_hours`, `prompt`, optional `skip_shorts` (boolean, default `true`), optional `language` (`en` default, or `es`; texts live in `texts.py`) |
| `channels.yml` | no — `channels.yml.sample` committed | list of `{name, channel_id, title_filter?: [str]}` |
| `storage_state.json` | never | NotebookLM session cookies (Playwright storage state) |
| `seen.json` | never (in `STATE_DIR`) | handled ids, unsent messages, failure counts, auth-alert flag |
| `scripts/server.env` | no — `scripts/server.env.sample` committed | `SERVER_SSH_TARGET`, `SERVER_YST_HOME` for the desktop-side scripts and skills |

- Server layout, all owned by the server user, nothing shared with any other app:

  ```
  $YST_HOME/
    repo.git/                 bare repo, hooks/post-receive
    app/                      checkout (written by the hook only)
    config/                   .env, config.yml, channels.yml
    state/                    seen.json
    secrets/notebooklm/       storage_state.json (+ lock files written by the library)
  ```

- The real files live outside the checkout and are mounted, so a push never
  overwrites them. `.gitignore` covers all real files.
- Config is loaded and validated at startup. Missing or invalid values fail fast
  with a message naming the file and the key. Stdlib dataclasses + PyYAML; no pydantic.
  Changing `config.yml` or `channels.yml` needs `docker compose restart`.
- The NotebookLM library writes refreshed cookies back to `storage_state.json` (atomic
  temp-file + rename) and writes lock files next to it. So the **directory**
  `secrets/notebooklm/` is mounted **read-write**, never the single file.
- The service uses its **own Telegram bot** (BotFather), added to the group. It only
  sends; it never polls updates.
- NotebookLM runs on a **dedicated Google account** (not the previous system's). The
  notebook has a custom chat persona (`chat.configure`, up to 10,000 chars) holding the
  stable instructions. The per-video `prompt` in `config.yml` holds the JSON contract.
  Both are private: the persona lives in the Google account, the prompt in untracked
  config.
- On the desktop the session lives in its own NotebookLM profile
  (`NOTEBOOKLM_PROFILE=youtube-summary` → `~/.notebooklm/profiles/youtube-summary/`),
  separate from any other app's profile.

## Behaviour of one iteration

The loop runs one iteration, then sleeps `poll_interval_minutes` (the sleep ends early
on SIGTERM). One iteration:

1. **Load state.** If `seen.json` does not exist: **bootstrap**. Fetch every channel's
   feed, mark every entry as seen, save, send nothing, end the iteration. This prevents
   the first-run flood and reposting what the previous system already sent. A channel
   added later is not bootstrapped: its videos inside the age window are sent. If the
   file is empty or invalid JSON: rename it to `seen.json.corrupt-<UTC timestamp>`,
   log an error, and bootstrap.
2. **Keepalive, always** (inside and outside `active_hours`): `notebooks.list()` with a
   60 s timeout. It costs no chat query and lets the library rotate and save the
   session cookies. Auth failure → the auth-alert path (below), end the iteration.
   Other error → log a warning, continue. Success → clear `auth_alert_sent`.
3. Outside `active_hours`: save and end the iteration.
4. **Retry unsent messages** (`pending`, oldest first) with the send rule below. No
   NotebookLM call.
5. **Collect candidates.** For each channel: fetch the RSS feed, keep entries that are
   not Shorts when `skip_shorts` is true (alternate link path contains `/shorts/`), not seen, not pending, younger than `max_video_age_hours`, and match the channel's
   `title_filter` (case-insensitive substring match of any word; empty or missing =
   all videos). A feed that fails (HTTP error, timeout, malformed XML) is logged and
   skipped. One bad channel never stops the others.
6. **Process candidates oldest first.** Before each video, stop if SIGTERM arrived.
   - NotebookLM (900 s timeout): delete every source of the dedicated notebook that is not a
     document (kept kinds: pasted text, Markdown, PDF, Google Docs/Slides/Sheets, DOCX,
     PowerPoint, CSV, EPUB, Excel; unknown kinds are deleted too), add the
     video URL (wait until processed), ask the configured prompt over all sources. One chat query per
     video.
   - Video not processable yet (`SourceError`, e.g. a premiere or a live stream still
     running): leave it unseen; retried next iteration until it ages out. This costs
     no chat query.
   - Auth failure: the auth-alert path, end the iteration. Auth failures never count
     toward the failure cap and never drop a video; videos stay unseen and are processed
     after the session is renewed.
   - Chat quota exhausted (`RateLimitError` from the chat query only; the same error
     from source or notebook calls is an ordinary failure): log a warning, record no
     failure, end the iteration. The adapter pauses NotebookLM calls for `retry_after`
     (else 1 h): while paused, `summarize` fails at once without calling NotebookLM.
     Videos stay unseen and are retried until they age out.
   - Other NotebookLM error or timeout: increment the video's failure count. At 3
     failures, log an error and mark it seen (given up). Otherwise retry next iteration.
   - Success: clear its failure count, build the message, split it if > 4096 chars,
     store the chunks in `pending` **and save before the first send**, then send with
     the send rule.
7. **Prune**: drop `seen` and failure entries older than `max_video_age_hours + 24` h.
   Older videos are ignored by step 5 anyway, so pruning cannot cause resends.
   `pending` and failure entries are bounded by their 3-attempt caps.
8. Every write of `seen.json` is atomic (temp file in the same dir + `os.replace`).

**Send rule** (new and pending messages). Send the remaining chunks in order. After
each chunk Telegram accepts, remove it from `pending` and save, so an accepted chunk is
never resent. When a chunk is rejected: increment `send_attempts`, save, stop sending
this video until the next iteration. When `send_attempts` reaches 3: log an error,
drop it from `pending`, mark it seen. When no chunk is left: drop it from `pending`,
mark it seen, save. NotebookLM is never called again for a pending video.

**Auth-alert path.** Unless `auth_alert_sent`, send one warning to the chat and, if
Telegram accepted it, set `auth_alert_sent` and save. Videos stay unseen and are
processed after the session is renewed, if still inside the age window.

- Shorts are skipped by default (never summarised or sent; `skip_shorts: false` processes them; bootstrap may still mark them seen, which is
  harmless). Live streams and
  premieres get no special handling.
- `active_hours` are inclusive on both ends, by local hour in `timezone`. Example:
  start 7, end 23 → summarises and sends from 07:00 to 23:59.
- Logs go to stdout. One line per video outcome plus one summary line per iteration.

## Message format (Telegram HTML)

```
🎬 <b><channel name></b> — <video title>

<summary>

• <key point>
• <key point>

🔗 <a href="<url>">Watch video</a>
```

- Fixed texts (`Untitled`, `Watch video`, the auth alert) live in `texts.py` in English and
  Spanish (`Sin título`, `Ver vídeo`); `language` in `config.yml` picks one.
- `parse_mode: "HTML"`. Dynamic text is escaped with `html.escape(text, quote=False)`,
  the URL inside `href` with `html.escape(url, quote=True)`. HTML only needs `<`, `>`,
  `&` (and `"` in attributes) escaped, so there is no hand-written escape table.
- The prompt is the owner's and is **never committed**: `config.yml.sample` holds only a
  placeholder plus a comment with the contract. The prompt must ask NotebookLM for JSON
  `{"summary": str, "key_points": [str]}` (summary ≤ 1500 chars recommended); any other
  answer is sent as plain text (below).
- NotebookLM sometimes wraps JSON in ```` ``` ```` fences or adds citation markers like
  `[1]` or `[1, 2]`. Strip both before parsing. If the answer still is not that JSON,
  send the cleaned raw answer text as the summary with no key points.
- A message longer than 4096 chars is split on sentence boundaries. A unit still
  longer than 4096 is cut at the last whitespace before the limit, else at the limit
  but never inside an `&…;` entity. Cuts never fall inside a tag, because tags only
  appear in the short header and footer units.

## Dependency updates

- `.github/dependabot.yml`, weekly with a 3-day cooldown, ecosystems `uv`, `docker`
  (base image digest), `github-actions`, `pre-commit`.
- `.github/workflows/dependabot-automerge.yml` enables auto-merge (squash) only for
  Python patch updates, GitHub Actions and pre-commit minor and patch updates, and
  Docker digest-only updates. `notebooklm-py`, Python minors, Docker tag changes and
  every major update stay manual: they need the live smoke check, because CI never
  talks to NotebookLM.
- Auto-merge is safe only with a ruleset on `main` that requires the CI checks
  (owner as bypass actor, so direct pushes keep working). Merging never deploys:
  deploy stays a manual `git push production main`.

## Repo contents

- `src/youtube_summary/` package, `tests/`, `pyproject.toml` (uv, `[dependency-groups]`),
  `uv.lock`, `.python-version` (`3.15`).
- `Dockerfile`, `.dockerignore`, `docker-compose.yml`.
- `.env.sample`, `config.yml.sample`, `channels.yml.sample`, `scripts/server.env.sample`,
  `.gitignore`.
- `scripts/ops/post-receive` (deploy hook), `scripts/notebooklm_session/sync_session.sh`
  (renew the NotebookLM session on a desktop and copy it to the server),
  `scripts/smoke_notebooklm.py` (manual live check).
- `README.md` for a public audience, `CLAUDE.md`, `.claude/skills/deploy/SKILL.md`,
  `.claude/skills/renew-notebooklm-session/SKILL.md`.
- `.github/workflows/ci.yml`: pre-commit (ruff format + lint), mypy --strict, pytest
  with coverage ≥ 90 %, image build. `.github/dependabot.yml`,
  `.github/workflows/dependabot-automerge.yml`.
- `LICENSE`: Apache 2.0 (already in the repo).

## Decommissioning the previous system

Out of scope for this repo. The owner has already committed, in the private repo, the
change that stops the old YouTube schedules. It is deployed right after this service
has bootstrapped on the server. If it was deployed earlier, videos published between
that deploy and the bootstrap are marked seen and never sent; that loss is accepted.
The owner exports the old channel list (id + title filter) into `channels.yml`. Nothing
of this service reads or writes anything belonging to the previous system.

## Left to the builder

- Internal module layout beyond what the plan fixes, and test helpers.
- Tests use their own short dummy prompt; no real prompt exists anywhere in the repo.

## Private copy

The owner keeps a private copy of this repo holding the real `config.yml` (with the
prompt), `channels.yml` and other private values. GitHub does not allow a private
fork of a public repo, so the copy is a separate private repo with this one added as
`upstream` remote (`git pull upstream main`). In that copy the owner un-ignores those
files and relaxes `tests/test_repo_hygiene.py`. The public repo stays free of them.

## Appendix A — reference code from the previous system

Copy these and adapt the names. They have run in production for months.

### Telegram split on sentence boundaries

```python
TELEGRAM_MESSAGE_LIMIT = 4096


def split_for_telegram(text: str, max_chars: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    units = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip() if current else unit
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
```

Known limit: a single sentence longer than 4096 chars stays as one oversized chunk.
The new service must cut it as described in "Message format". The plan has a test.
The previous system used MarkdownV2 with a hand-written escape table that missed `\`;
this service uses HTML instead, so that table is not reproduced.

### YouTube RSS feed parsing

Feed URL: `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`.
The feed lists the latest ~15 uploads.

```python
_ATOM_NS = "http://www.w3.org/2005/Atom"
_YT_NS = "http://www.youtube.com/xml/schemas/2015"


def _parse_feed(xml_text: str) -> list[...]:
    root = ElementTree.fromstring(xml_text)
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        video_id = _text(entry, f"{{{_YT_NS}}}videoId")
        published_raw = _text(entry, f"{{{_ATOM_NS}}}published")
        link_el = entry.find(f"{{{_ATOM_NS}}}link[@rel='alternate']")
        if video_id is None or published_raw is None or link_el is None:
            continue
        url = link_el.attrib.get("href", "")
        published_at = datetime.fromisoformat(published_raw)  # tz-aware, normalise to UTC
        title = _text(entry, f"{{{_ATOM_NS}}}title")
        ...
```

### Telegram send

`POST https://api.telegram.org/bot{token}/sendMessage` with JSON
`{"chat_id", "text", "parse_mode": "HTML", "disable_web_page_preview": true}`,
timeout 10 s. HTTP 200 = sent. Any other status: log status + first 300 chars of the
body, return False. Any exception: log the exception type and message (never the
URL), return False. Never raise.

### NotebookLM flow (notebooklm-py)

```python
from notebooklm import NotebookLMClient
from notebooklm.exceptions import AuthError, HeadlessReauthError, NotebookLMError, SourceError

async with NotebookLMClient.from_storage(str(storage_state_path)) as client:
    sources = await client.sources.list(notebook_id)
    stale_ids = [s.id for s in sources if s.kind not in DOCUMENT_KINDS]   # documents are permanent reference sources
    if stale_ids:
        await client.sources.delete_many(notebook_id, stale_ids)
    await client.sources.add_url(notebook_id, video_url, wait=True, wait_timeout=600)   # raises SourceError if not processable
    conversation_id = await client.chat.get_conversation_id(notebook_id)
    if conversation_id is not None:
        await client.chat.delete_conversation(notebook_id, conversation_id)
    result = await client.chat.ask(notebook_id, prompt)
    answer: str = result.answer

# keepalive
async with NotebookLMClient.from_storage(str(storage_state_path)) as client:
    await client.notebooks.list()
```

- `chat.ask` without a `conversation_id` continues the notebook's current conversation,
  so each video would see earlier videos' Q&A. Delete the current conversation before
  every ask to keep each summary independent.
- Use `from_storage(...)` directly as the async context manager (canonical form).
  Do **not** `await from_storage(...)`: that form is deprecated and removed in 1.0.
- `AuthError` is a subclass of `RPCError` and `SourceTimeoutError` of `SourceError`;
  catch `SourceError` and the auth errors before the generic `NotebookLMError`.
- Google's `__Secure-1PSIDTS` cookies are valid for minutes to hours and plain RPC
  traffic does not rotate them. The previous system stayed logged in because a health
  check called `notebooks.list()` every 30 minutes. This service does the same every
  iteration (step 2). Confirm in the live smoke check that `storage_state.json`'s
  mtime changes after a keepalive; if it does not, pass `keepalive=` to `from_storage`
  or report it in the README troubleshooting section.
- The previous system used `notebooklm-py 0.8.0rc1` (vendored wheel). PyPI has `0.8.6`;
  pin `notebooklm-py==0.8.6` from PyPI, no vendoring.
- The session file is created by `notebooklm login` on a desktop (the CLI ships with
  the package; login uses a browser via Playwright). With
  `NOTEBOOKLM_PROFILE=youtube-summary` it is written to
  `~/.notebooklm/profiles/youtube-summary/storage_state.json`.

### Deploy hook behaviour (`post-receive`)

The previous system's hook, minus its database migrations. Reproduce this behaviour:

- `set -eo pipefail`. Paths come from `YST_HOME` (default `$HOME/youtube-summary-telegram`):
  `GIT_DIR=$YST_HOME/repo.git`, `APP_DIR=$YST_HOME/app`.
- Read `oldrev newrev refname` lines from stdin. Deploy only when `refs/heads/main` is
  created or updated; a deletion (`newrev` all zeros), tags and other branches print
  `==> No deploy: no update of refs/heads/main (received: <refs>)` and exit 0.
- Manual run: when stdin is a TTY or `POST_RECEIVE_FORCE=1`, deploy without reading refs.
- `POST_RECEIVE_DRY_RUN=1` prints `[DRY RUN] <command>` instead of running commands.
- Deploy: `git --work-tree="$APP_DIR" --git-dir="$GIT_DIR" checkout -f main`,
  `cd "$APP_DIR"`, `docker compose --env-file "$YST_HOME/config/.env" up -d --build`,
  `docker compose --env-file ... ps`, then
  `docker image prune -f --filter label=com.docker.compose.project=youtube-summary-telegram`
  (only this project's dangling images; no reliance on any host-wide prune job), then
  `==> Deploy complete!`. It never touches `config/`, `state/` or `secrets/`.
- Rollback: `git push production <previous-sha>:main --force` (the image is small and
  rebuilds in seconds).
