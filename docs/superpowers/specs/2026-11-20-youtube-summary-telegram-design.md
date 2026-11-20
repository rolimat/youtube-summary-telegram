# youtube-summary-telegram — design

Date: 2026-11-20
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
- Runs as one Docker container on the owner's home Linux server, deployed by
  `git push` to a bare repo whose `post-receive` hook runs `docker compose up -d --build`.
- Public repo: explanatory `README.md`, `CLAUDE.md`, Claude Code skills, GitHub Actions CI.

## Non-goals

- No database, no stored summaries, no UI, no HTTP API.
- No ticker resolution or financial signal extraction.
- No weekly or aggregated summaries.

## Anti-goals

- Any credential, cookie, chat id, notebook id, hostname, username or server path of
  the owner committed to the public repo. Samples use placeholders only.
- A video posted twice to the group.
- A first run that floods the group with every channel's back catalogue.
- A state file that grows without bound.

## Approaches (decided: A)

- **A. Long-running container with an internal loop (chosen).** One Python process runs
  a cycle, sleeps `poll_interval_minutes`, repeats; it only works inside `active_hours`.
  `restart: unless-stopped`. Deploy is `git push` + hook. Cost: ~10 lines of own
  scheduling code.
- B. One-shot container fired by a host systemd timer/cron. No resident process, but
  the timer lives outside the deploy hook and failures are less visible. Rejected.
- C. Celery + Redis. Far too much for an hourly poll. Rejected.

## Configuration

| File | In git | Content |
|---|---|---|
| `.env` | no — `.env.sample` committed | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTEBOOKLM_STORAGE_STATE_PATH`, `CONFIG_DIR`, `STATE_DIR` |
| `config.yml` | no — `config.yml.sample` committed | `notebooklm.notebook_id`, `poll_interval_minutes`, `active_hours.{start,end,timezone}`, `max_video_age_hours`, `prompt` |
| `channels.yml` | no — `channels.yml.sample` committed | list of `{name, channel_id, title_filter?: [str]}` |
| `storage_state.json` | never | NotebookLM session cookies (Playwright storage state) |
| `seen.json` | never (in `STATE_DIR`) | handled video ids + auth-alert flag |

- On the server the real files live outside the git checkout and are mounted into the
  container, so a push never overwrites them. `.gitignore` covers all real files.
- Config is loaded and validated at startup. Missing or invalid values fail fast
  with a message naming the file and the key. Stdlib dataclasses + PyYAML; no pydantic.
- The NotebookLM library writes refreshed cookies back to `storage_state.json` (atomic
  temp-file + rename). So the **directory** holding it is mounted **read-write**, never
  the single file. A single-file bind mount breaks the rename.
- The app only sends to Telegram; it never polls updates, so it can share a bot token
  with another app that polls.

## Behaviour of one cycle

1. If `seen.json` does not exist: **bootstrap**. Fetch every channel's feed, mark every
   entry as seen, save, send nothing, return. This prevents the first-run flood and
   reposting what the previous system already sent. A channel added later is not
   bootstrapped: its videos inside the age window are sent.
2. For each channel: fetch the RSS feed, keep entries that are not seen, are younger
   than `max_video_age_hours`, and match the channel's `title_filter`
   (case-insensitive substring match of any word; empty or missing = all videos).
   A feed that fails (HTTP error, timeout, malformed XML) is logged and skipped. One
   bad channel never stops the others.
3. Process candidates oldest first:
   - NotebookLM: delete every source in the dedicated notebook, add the video URL
     (wait until processed), ask the configured prompt.
   - Video not processable yet (NotebookLM `SourceError`, e.g. a premiere or a live
     stream still running): leave it unseen; retried next cycle until it ages out.
   - NotebookLM auth failure: stop the cycle (every other video would fail too). Send
     one warning to the Telegram chat unless already sent, and set the flag
     `auth_alert_sent`. Videos stay unseen and are processed after the session is
     renewed, if still inside the age window.
   - Other NotebookLM error: log, leave unseen, continue with the next video.
   - Build the message, split if > 4096 chars, send every chunk.
   - Mark seen **only after** Telegram accepted every chunk; save `seen.json` after each
     video so a crash never causes a resend.
4. After any successful NotebookLM call, clear `auth_alert_sent` (and save).
5. Prune `seen.json`: drop entries older than `max_video_age_hours + 24` hours. Older
   videos are ignored by step 2 anyway, so pruning cannot cause resends.
6. Every write of `seen.json` is atomic (temp file in the same dir + `os.replace`).

- Shorts, live streams and premieres get no special handling.
- `active_hours` are inclusive on both ends, by local hour in `timezone`. Example:
  start 7, end 23 → runs from 07:00 to 23:59.
- Logs go to stdout. One line per video outcome plus one summary line per cycle.

## Message format (Telegram MarkdownV2)

```
🎬 *<channel name>* — <video title>

<summary>

• <key point>
• <key point>

🔗 [Ver vídeo](<url>)
```

- The prompt asks NotebookLM for JSON `{"summary": str, "key_points": [str]}` in
  Spanish. The default prompt ships in `config.yml.sample` and can be changed freely.
- NotebookLM sometimes wraps JSON in ```` ``` ```` fences or adds citation markers like
  `[1]` or `[1, 2]`. Strip both before parsing. If the answer still is not that JSON,
  send the cleaned raw answer text as the summary with no key points.
- All dynamic text is MarkdownV2-escaped. Inside the link URL, only `\` and `)` are escaped.
- A message longer than 4096 chars is split on sentence boundaries.

## Repo contents

- `src/youtube_summary/` package, `tests/`, `pyproject.toml` (uv), `uv.lock`.
- `Dockerfile`, `docker-compose.yml`.
- `.env.sample`, `config.yml.sample`, `channels.yml.sample`, `.gitignore`.
- `scripts/ops/post-receive` (deploy hook), `scripts/notebooklm_session/sync_session.sh`
  (renew the NotebookLM session on a desktop and copy it to the server) with its own
  `.env.sample`.
- `README.md` for a public audience, `CLAUDE.md`, `.claude/skills/deploy/SKILL.md`,
  `.claude/skills/renew-notebooklm-session/SKILL.md`.
- `.github/workflows/ci.yml`: pre-commit (ruff format + lint), mypy --strict, pytest
  with coverage ≥ 90 %.
- `LICENSE`: Apache 2.0 (already in the repo).

## Decommissioning the previous system

This step is out of scope for this repo. The owner does it in the private repo,
immediately after the first deploy of this service, so any overlap lasts only minutes.
It stops the old discover, analyze, digest, weekly-summary, prune and auth-monitor
schedules. The owner also exports the old channel list (id + title filter) into
`channels.yml` on the server.

## Left to the builder

- Internal module layout beyond what the plan fixes, and test helpers.
- Exact default prompt wording (Spanish, JSON contract above).

## Appendix A — reference code from the previous system

Copy these and adapt the names. They have run in production for months.

### MarkdownV2 escaping

```python
def escape_md(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def escape_url(url: str) -> str:
    return url.replace("\\", "\\\\").replace(")", "\\)")
```

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
The new service must hard-cut such a chunk at 4096 chars. The plan has a test for it.

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
`{"chat_id", "text", "parse_mode": "MarkdownV2", "disable_web_page_preview": true}`,
timeout 10 s. HTTP 200 = sent. Any other status: log status + first 300 chars of the
body, return False. Any exception: log, return False. Never raise.

### NotebookLM flow (notebooklm-py)

```python
from notebooklm import NotebookLMClient, SourceError, AuthError

async with NotebookLMClient.from_storage(str(storage_state_path)) as client:
    for source in await client.sources.list(notebook_id):
        await client.sources.delete(notebook_id, source.id)
    await client.sources.add_url(notebook_id, video_url, wait=True)   # raises SourceError if not processable
    result = await client.chat.ask(notebook_id, prompt)
    answer: str = result.answer
```

The previous system used `notebooklm-py 0.8.0rc1`. PyPI now has `0.8.6`. Pin
`notebooklm-py==0.8.6` and confirm the calls above in Task 6's live smoke check. If an
import or signature differs, adapt to 0.8.6 and record the difference in the README.
The session file is created by `notebooklm login` on a desktop. The CLI ships with the
same package, and the login uses a browser (Playwright). It is written to
`~/.notebooklm/profiles/default/storage_state.json`.
