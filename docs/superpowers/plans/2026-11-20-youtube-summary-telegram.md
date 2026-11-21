# youtube-summary-telegram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-container Python service that polls YouTube channel RSS feeds, summarises new videos with NotebookLM and posts each summary to a Telegram group, with no database, no UI and no dependency on any other system.

**Architecture:** One asyncio process (`youtube-summary`) loops forever. Each iteration keeps the NotebookLM session alive (`notebooks.list()`), and inside the active hours it retries unsent messages, reads the feeds, filters out seen, pending, old and off-topic videos, summarises each remaining video and sends it to Telegram. It records handled ids, unsent message chunks and failure counts in a small, pruned `seen.json`. Pure parts (config, feed parsing, filtering, state, message formatting) are plain functions or classes. The three I/O edges (feed HTTP, NotebookLM, Telegram) are thin adapters behind `Protocol`s, so the iteration is unit-tested with fakes.

**Tech Stack:** Python 3.15 (latest stable), uv, httpx, PyYAML, tzdata, notebooklm-py 0.8.6; pytest, pytest-asyncio, pytest-cov, mypy (strict), ruff, pre-commit; Docker + docker compose; GitHub Actions; Dependabot.

**Spec:** [`docs/superpowers/specs/2026-11-20-youtube-summary-telegram-design.md`](../specs/2026-11-20-youtube-summary-telegram-design.md). Read it first. Its Appendix A holds the reference code and behaviour to copy.

## Global Constraints

- Python `>=3.15` (`.python-version` = `3.15`, ruff `target-version = "py315"`, mypy `python_version = "3.15"`); package `youtube_summary` under `src/`; console script `youtube-summary = "youtube_summary.main:main"`.
- Runtime dependencies exactly: `httpx`, `pyyaml`, `tzdata`, `notebooklm-py==0.8.6`. Nothing else at runtime.
- Dev dependencies in `[dependency-groups] dev` (PEP 735, installed by plain `uv sync`): `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-cov`, `mypy`, `types-PyYAML`, `ruff`, `pre-commit`.
- `mypy --strict` clean on `src/` and `tests/`. Ruff line length 100.
- Functions ≤ 20 lines, full type hints, no comments that restate code, OOP where state exists.
- TDD: one failing test first, then the code, for every behaviour.
- No test touches the network: feeds and Telegram use `httpx.MockTransport`; NotebookLM uses a fake client factory.
- Coverage gate: `--cov=youtube_summary --cov-fail-under=90`.
- No real token, chat id, notebook id, hostname, username, server path or name of the previous system in any committed file. Samples use `<placeholders>`.
- Nothing reads or writes files, directories, sessions, bots or containers of the previous system.
- Telegram: `parse_mode="HTML"`, `disable_web_page_preview=true`, 4096-char limit, 10 s timeout.
- NotebookLM: summarise timeout 900 s, keepalive timeout 60 s.
- Attempt caps: `MAX_SEND_ATTEMPTS = 3`, `MAX_SUMMARIZE_FAILURES = 3`.
- Logging: `httpx` and `httpcore` loggers at WARNING (the Telegram URL holds the token).
- Defaults in `config.yml.sample`: `poll_interval_minutes: 15`, `active_hours: {start: 7, end: 23, timezone: Europe/Madrid}`, `max_video_age_hours: 48`.
- `seen.json` pruning horizon: `max_video_age_hours + 24` hours.
- Commit after each task with Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`, `ci:`), run `pre-commit run --all-files` and the full test suite first.

## Review Focus

1. **Corrupt or empty `seen.json`** (disk full, manual edit): expected no crash loop and no flood. The file is renamed to `seen.json.corrupt-<UTC timestamp>` and the iteration bootstraps. Test is in Task 4.
2. **A single sentence longer than 4096 chars** (NotebookLM answer without punctuation): expected every chunk ≤ 4096 and no cut inside an `&…;` entity. Test is in Task 5.
3. **Titles and summaries full of HTML specials** (`S&P 500 <live> "2025"`): expected escaped text and an `href` whose URL contains `&` and `"`. Test is in Task 5.
4. **One channel feed fails** (deleted channel → 404, timeout, malformed XML): expected the other channels to still be processed and the failure logged. Test is in Task 7.
5. **Telegram rejects the second chunk of a split message**: expected the first chunk is never resent, NotebookLM is not called again, the second chunk is retried next iteration, and after 3 rejected attempts the video is marked seen and dropped. Test is in Task 7.
6. **NotebookLM session with no new videos for days**: expected `notebooks.list()` every iteration, also outside active hours. Test is in Task 7.
7. **SIGTERM during a deploy**: expected no new video or send starts after the signal, and the process exits. Tests are in Tasks 7 and 8.

---

## File Structure

```
src/youtube_summary/
  __init__.py
  __main__.py          # `python -m youtube_summary` → main()
  config.py            # Channel, ActiveHours, AppConfig, Settings, ConfigError, loaders
  feed.py              # Video, FeedError, parse_feed, fetch_channel_videos, matches_title_filter
  state.py             # SeenState, PendingMessage (load/save/prune, pending, failures, auth flag)
  message.py           # Summary, parse_answer, format_message, format_auth_alert, split_for_telegram
  telegram.py          # TelegramClient
  summarizer.py        # NotebookLMSummarizer, VideoNotReadyError, NotebookAuthError, SummarizerError
  cycle.py             # CycleDeps, CycleResult, run_cycle
  main.py              # is_within_active_hours, run_forever, build_deps, main
tests/                 # test_<module>.py per module, conftest.py with shared builders
Dockerfile, .dockerignore, docker-compose.yml, .env.sample, config.yml.sample, channels.yml.sample
scripts/server.env.sample
scripts/ops/post-receive
scripts/notebooklm_session/sync_session.sh
scripts/smoke_notebooklm.py
.claude/skills/deploy/SKILL.md, .claude/skills/renew-notebooklm-session/SKILL.md
.github/workflows/ci.yml, .github/workflows/dependabot-automerge.yml, .github/dependabot.yml
CLAUDE.md, README.md, .pre-commit-config.yaml, .gitignore
```

---

### Task 1: Project skeleton, tooling, CI and CLAUDE.md

**Files:**
- Create: `pyproject.toml`, `uv.lock` (generated), `.python-version` (`3.15`), `.gitignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `CLAUDE.md`, `src/youtube_summary/__init__.py`, `src/youtube_summary/__main__.py`, `src/youtube_summary/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `youtube_summary.main.main() -> None` (temporary body: `raise SystemExit(0)`; replaced in Task 8).

- [ ] **Step 1: Write `pyproject.toml`.** Build backend `uv_build` with a src layout. Add the runtime dependencies from Global Constraints and `[dependency-groups] dev = [...]`. Set `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`. Set `[tool.ruff] line-length = 100, target-version = "py315"`, `[tool.ruff.lint] select = ["E","F","I","UP","B","SIM","RUF"]`. Set `[tool.mypy] strict = true`, `python_version = "3.15"`, `mypy_path = "src"`.
- [ ] **Step 2: `uv lock && uv sync`.** Expected: `.venv` created with dev group, `uv.lock` written.
- [ ] **Step 3: Write the failing test** `tests/test_main.py::test_main_module_is_importable`:

```python
def test_main_module_is_importable() -> None:
    from youtube_summary.main import main
    assert callable(main)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_main.py -q`. Expected: FAIL (ModuleNotFoundError).
- [ ] **Step 5: Create the package files** with the temporary `main()`. `__main__.py` calls `main()`.
- [ ] **Step 6: Run** the test again. Expected: PASS.
- [ ] **Step 7: `.gitignore`** must contain: `.venv/`, `__pycache__/`, `.coverage`, `.env`, `config.yml`, `channels.yml`, `local/`, `state/`, `secrets/`, `seen.json*`, `storage_state.json`, `scripts/server.env`.
- [ ] **Step 8: `.pre-commit-config.yaml`** with hooks `ruff` (with `--fix`), `ruff-format`, `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `detect-private-key`, and a local `mypy` hook running `uv run mypy src tests`. Every remote hook has a pinned `rev` (Dependabot bumps them).
- [ ] **Step 9: `.github/workflows/ci.yml`.** Trigger on push and pull_request to `main`; set `permissions: contents: read` and a concurrency group. Job `test`: `actions/checkout` and `astral-sh/setup-uv` at their latest major (cache on), `uv python install` (reads `.python-version`), `uv sync --frozen`, `uv run pre-commit run --all-files`, `uv run pytest -q --cov=youtube_summary --cov-fail-under=90 --cov-report=term-missing:skip-covered`. Job `image` (`needs: test`): `docker build -t youtube-summary:ci .`. These two job names are the required checks in Task 10.
- [ ] **Step 10: `CLAUDE.md`.** Short sections:
  - **Core principles:** baby steps, TDD (one failing test at a time), full typing, small functions (≤ 20 lines), clear names, no comments, simplest solution, flag repeated code.
  - **Commands:** `uv sync`, `uv run pytest -q`, `uv run mypy src tests`, `uv run pre-commit run --all-files`, `uv run youtube-summary --once`.
  - **Testing:** mock every external API (YouTube, NotebookLM, Telegram); `httpx.MockTransport` for HTTP; no network in tests.
  - **Security:** never commit `.env`, `config.yml`, `channels.yml`, `storage_state.json`, `seen.json`, `scripts/server.env`, tokens, chat ids, notebook ids, hostnames, usernames, server paths.
  - **Independence:** this service never depends on another app's files, sessions, bots or containers.
  - **NotebookLM budget:** usage is limited; never add code paths that call NotebookLM again for a video already summarised.
  - **Dependencies:** Dependabot PRs; `notebooklm-py` and major updates need `scripts/smoke_notebooklm.py` before merging.
  - **Validation before every commit:** pre-commit + full pytest, zero errors.
  - **Commits:** Conventional Commits.
  - **Docs map:** pointers to `README.md` and the spec/plan under `docs/superpowers/`.
- [ ] **Step 11: Validate and commit.** `uv run pre-commit run --all-files && uv run pytest -q`, then `git commit -m "chore: project skeleton, tooling, CI and CLAUDE.md"`.

---

### Task 2: Configuration loading

**Files:**
- Create: `src/youtube_summary/config.py`, `config.yml.sample`, `channels.yml.sample`, `.env.sample`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`. Its message always names the file and the offending key.
  - `@dataclass(frozen=True) class Channel: name: str; channel_id: str; title_filter: tuple[str, ...]`
  - `@dataclass(frozen=True) class ActiveHours: start: int; end: int; timezone: ZoneInfo`
  - `@dataclass(frozen=True) class AppConfig: notebook_id: str; poll_interval_minutes: int; active_hours: ActiveHours; max_video_age_hours: int; prompt: str; texts: Texts; skip_shorts: bool` (resolved from the optional YAML `language`, default `en`, must be a key of `texts.TEXTS`)
  - `@dataclass(frozen=True) class Settings: telegram_bot_token: str; telegram_chat_id: str; storage_state_path: Path; config_dir: Path; state_dir: Path`
  - `def load_app_config(path: Path) -> AppConfig`
  - `def load_channels(path: Path) -> tuple[Channel, ...]`
  - `def load_settings(environ: Mapping[str, str]) -> Settings`. Env keys: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `NOTEBOOKLM_STORAGE_STATE_PATH` are required. `CONFIG_DIR` defaults to `/config`, `STATE_DIR` to `/state`.

YAML shapes:

```yaml
# config.yml
notebooklm:
  notebook_id: "<notebook-id-from-notebooklm-url>"
poll_interval_minutes: 15
active_hours: {start: 7, end: 23, timezone: Europe/Madrid}
max_video_age_hours: 48
language: en  # en | es
skip_shorts: true  # skip YouTube Shorts (set false to summarise them)
prompt: |
  <Spanish prompt asking for ONLY a JSON object {"summary": "...", "key_points": ["..."]}>
```

```yaml
# channels.yml
channels:
  - name: "Example Channel"
    channel_id: "UCxxxxxxxxxxxxxxxxxxxxxx"
    title_filter: ["bolsa", "inflación"]   # optional
```

- [ ] **Step 1: Failing test** `test_load_app_config_reads_all_fields`. Write the sample-shaped YAML to `tmp_path`. Assert `notebook_id`, `poll_interval_minutes == 15`, `active_hours.timezone == ZoneInfo("Europe/Madrid")`, `max_video_age_hours == 48`, and that `prompt` is non-empty.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `load_app_config`. **Step 4: Run** → PASS.
- [ ] **Step 5: Failing test** `test_load_app_config_rejects_missing_notebook_id`: `pytest.raises(ConfigError, match="notebooklm.notebook_id")`. Implement; also reject `start`/`end` outside 0–23, `start > end`, non-positive interval or age, and an unknown timezone. Use one parametrized test `test_load_app_config_rejects_invalid_values` with those 5 cases.
- [ ] **Step 6: Failing test** `test_load_channels_defaults_title_filter_to_empty` (a channel without `title_filter` → `()`), then `test_load_channels_rejects_channel_without_channel_id` (`match="channel_id"`), then `test_load_channels_rejects_duplicate_channel_id`. Implement one at a time.
- [ ] **Step 7: Failing test** `test_load_settings_requires_telegram_token` (`match="TELEGRAM_BOT_TOKEN"`) and `test_load_settings_applies_dir_defaults` (`config_dir == Path("/config")`, `state_dir == Path("/state")`). Implement.
- [ ] **Step 8: Write the three sample files.**
  - `config.yml.sample`: the defaults above and **no real prompt**. `prompt` holds only a placeholder (`"<your prompt: ask for ONLY a JSON object {\"summary\": str, \"key_points\": [str]}>"`) with a YAML comment describing the JSON contract that `parse_answer` expects. The owner's real prompt lives only in the untracked `config.yml` (and in their private copy of the repo).
  - `channels.yml.sample`: two placeholder channels, one with `title_filter`.
  - `.env.sample`: `TELEGRAM_BOT_TOKEN=<bot-token-of-this-service's-own-bot>`, `TELEGRAM_CHAT_ID=<chat-id>`, `YST_HOME=<absolute dir holding repo.git/ app/ config/ state/ secrets/ on the host>`, commented `YST_UID=1000` / `YST_GID=1000`. Add commented `NOTEBOOKLM_STORAGE_STATE_PATH`, `CONFIG_DIR`, `STATE_DIR` lines for running without Docker.
- [ ] **Step 9: Test** `test_sample_files_load` loads `config.yml.sample` and `channels.yml.sample` from the repo root with the real loaders. It keeps the samples valid forever.
- [ ] **Step 10: Validate and commit** `feat: configuration loading and sample files`.

---

### Task 3: Feed parsing, fetching and title filter

**Files:**
- Create: `src/youtube_summary/feed.py`
- Test: `tests/test_feed.py`, fixture `tests/fixtures/feed.xml` (a real-shaped Atom feed with 3 entries, one missing `yt:videoId`)

**Interfaces:**
- Consumes: `Channel` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class Video: video_id: str; url: str; title: str; published_at: datetime; channel_name: str` (`published_at` is UTC-aware; a missing title becomes `""`)
  - `class FeedError(Exception)`
  - `FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"`
  - `def parse_feed(xml_text: str, channel_name: str) -> list[Video]`: raises `FeedError` on malformed XML and skips incomplete entries (spec Appendix A).
  - `async def fetch_channel_videos(http: httpx.AsyncClient, channel: Channel) -> list[Video]`: GET with a 15 s timeout. Non-2xx and `httpx.HTTPError` raise `FeedError`, with the channel name in the message.
  - `def matches_title_filter(title: str, words: tuple[str, ...]) -> bool`: empty `words` → True; otherwise a `casefold()` substring match of any word.

- [ ] **Step 1: Failing test** `test_parse_feed_returns_complete_entries`: the fixture yields 2 videos with the expected ids, titles, URLs and UTC datetimes.
- [ ] **Step 2: Run → FAIL; implement** `parse_feed` from spec Appendix A; **run → PASS.**
- [ ] **Step 3: Failing test** `test_parse_feed_raises_feed_error_on_malformed_xml`. Implement.
- [ ] **Step 4: Failing test** `test_fetch_channel_videos_raises_on_404`. Build `httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))` and expect `FeedError` with `match="Example"`. Then `test_fetch_channel_videos_requests_channel_feed_url`: assert that the requested URL equals `FEED_URL_TEMPLATE.format(channel_id=...)`. Implement.
- [ ] **Step 5: Parametrized failing test** `test_matches_title_filter` with cases:
  - `("Hablemos de BOLSA", ("bolsa",), True)`
  - `("Vlog", ("bolsa",), False)`
  - `("Vlog", (), True)`
  - `("INFLACIÓN y tipos", ("inflación", "fed"), True)`

  Implement.
- [ ] **Step 6: Validate and commit** `feat: youtube rss feed parsing and title filter`.

---

### Task 4: Seen state

**Files:**
- Create: `src/youtube_summary/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass class PendingMessage: label: str; chunks: list[str]; send_attempts: int = 0` (`label` is `"<channel> — <title>"`, for logs)
  - `class SeenState`:
    - `@classmethod load(cls, path: Path) -> SeenState | None`: returns `None` when the file does not exist (the caller bootstraps). When the file is empty or not valid JSON: log an error, rename it to `seen.json.corrupt-<YYYYmmddTHHMMSSZ>` in the same directory, return `None`.
    - `@classmethod empty(cls, path: Path) -> SeenState`
    - `is_seen(self, video_id: str) -> bool`
    - `mark_seen(self, video_id: str, at: datetime) -> None` (`at` = the time it was handled, i.e. `now`)
    - `pending: dict[str, PendingMessage]` (insertion order = oldest first)
    - `record_summarize_failure(self, video_id: str, at: datetime) -> int` (returns the new count)
    - `clear_summarize_failure(self, video_id: str) -> None`
    - `prune(self, now: datetime, horizon: timedelta) -> None`: removes `seen` and failure entries with `at < now - horizon`.
    - `auth_alert_sent: bool` (read/write attribute)
    - `save(self) -> None`: atomic. Write `<path>.tmp` in the same dir, then `os.replace`. Create the parent dir if missing.
- File format:

```json
{"version": 1,
 "seen": {"<video_id>": "<ISO-8601 UTC>"},
 "pending": {"<video_id>": {"label": "...", "chunks": ["..."], "send_attempts": 0}},
 "summarize_failures": {"<video_id>": {"count": 1, "at": "<ISO-8601 UTC>"}},
 "auth_alert_sent": false}
```

- [ ] **Step 1: Failing test** `test_load_returns_none_when_file_missing`. Implement. Run.
- [ ] **Step 2: Failing test** `test_save_then_load_round_trips`. Mark 2 ids, add one pending message with 2 chunks and `send_attempts=1`, record one summarize failure, set `auth_alert_sent = True`, save, load; assert all of it survives. Implement.
- [ ] **Step 3: Failing test** `test_prune_drops_entries_older_than_horizon`. One seen id and one failure 73 h old, one seen id 1 h old, horizon 72 h; only the recent one stays. Implement.
- [ ] **Step 4: Failing test** `test_record_summarize_failure_counts_up_and_clears`. Implement.
- [ ] **Step 5: Failing test** `test_load_quarantines_corrupt_file` (Review Focus 1). Write `"{not json"`; assert that `load` returns `None`, the original path is gone, and exactly one `seen.json.corrupt-*` file exists. Add `test_load_quarantines_empty_file` (0 bytes). Implement.
- [ ] **Step 6: Failing test** `test_save_leaves_no_tmp_file`. Implement.
- [ ] **Step 7: Validate and commit** `feat: pruned atomic state with pending messages`.

---

### Task 5: Message parsing and formatting

**Files:**
- Create: `src/youtube_summary/message.py`
- Test: `tests/test_message.py`

**Interfaces:**
- Consumes: `Video` (Task 3).
- Produces:
  - `@dataclass(frozen=True) class Summary: text: str; key_points: tuple[str, ...]`
  - `def parse_answer(answer: str) -> Summary`: strip ```` ``` ```` / ```` ```json ```` fences and citation markers matching `\s*\[\d+(?:\s*[,-]\s*\d+)*\]`, then `json.loads`. If the result is a dict with a non-empty string `summary`, return it with the `key_points` that are non-empty strings. Otherwise return `Summary(text=<cleaned answer stripped>, key_points=())`.
  - `def format_message(video: Video, summary: Summary, texts: Texts, max_chars: int = 4096) -> list[str]`: the HTML layout from the spec. Header `🎬 <b>{channel}</b> — {title}`; an empty title shows `texts.untitled`. Text goes through `html.escape(..., quote=False)`. Key-point lines are `• {point}`, and the key-points block is omitted when empty. The final line is `🔗 <a href="{html.escape(url, quote=True)}">{texts.watch_video}</a>`. Blocks are joined by `"\n\n"`.
  - `def split_for_telegram(text: str, max_chars: int = 4096) -> list[str]`: spec Appendix A plus the cut rule from the spec "Message format": cut at the last whitespace before `max_chars`, else at `max_chars` moved back before an unterminated `&…;` entity.
  - `def format_auth_alert(texts: Texts) -> str`: the HTML-escaped `texts.auth_alert`

- [ ] **Step 1: Failing test** `test_parse_answer_reads_json`. Implement. Run.
- [ ] **Step 2: Failing test** `test_parse_answer_strips_fences_and_citations`. Input: ```` ```json\n{"summary": "Sube [1].", "key_points": ["A [2, 3]"]}\n``` ````. Expected: `Summary("Sube.", ("A",))`. Implement.
- [ ] **Step 3: Failing test** `test_parse_answer_falls_back_to_raw_text` (`"Texto libre [1]"` → `Summary("Texto libre", ())`). Implement.
- [ ] **Step 4: Failing test** `test_format_message_escapes_html_specials` (Review Focus 3). Use title `S&P 500 <live> "2025"`, a summary with `a < b & c`, and URL `https://youtu.be/x?a=1&b="2"`. Assert the header contains `S&amp;P 500 &lt;live&gt; "2025"`, the summary contains `a &lt; b &amp; c`, and the link is `<a href="https://youtu.be/x?a=1&amp;b=&quot;2&quot;">Watch video</a>` (English texts). Implement.
- [ ] **Step 5: Failing test** `test_format_message_omits_empty_key_points` (no `•` in the output). Implement.
- [ ] **Step 6: Failing tests** for the split (Review Focus 2):
  - `test_split_for_telegram_hard_cuts_oversized_sentence`: `"a" * 9000` → 3 chunks, all ≤ 4096, joined back equals the input.
  - `test_split_for_telegram_never_cuts_inside_entity`: `"a" * 4094 + "&amp;" + "b" * 10` → no chunk ends with a partial entity, and every chunk ≤ 4096.
  - `test_split_for_telegram_splits_on_sentences`.

  Implement.
- [ ] **Step 7: Validate and commit** `feat: telegram html message formatting`.

---

### Task 6: Telegram client and NotebookLM summarizer

**Files:**
- Create: `src/youtube_summary/telegram.py`, `src/youtube_summary/summarizer.py`, `scripts/smoke_notebooklm.py`
- Test: `tests/test_telegram.py`, `tests/test_summarizer.py`

**Interfaces:**
- Produces:
  - `class TelegramClient: __init__(self, bot_token: str, chat_id: str, http: httpx.AsyncClient)`; `async def send(self, text: str) -> bool` (spec Appendix A "Telegram send"; never raises; never logs the URL).
  - `class VideoNotReadyError(Exception)`, `class NotebookAuthError(Exception)`, `class SummarizerError(Exception)`
  - `class NotebookLMSummarizer: __init__(self, storage_state_path: Path, notebook_id: str, client_factory: Callable[[str], AbstractAsyncContextManager[Any]] | None = None)`. The default factory is `NotebookLMClient.from_storage`, used as `async with factory(path) as client` (no `await`; spec Appendix A).
  - `async def summarize(self, video_url: str, prompt: str) -> str`. Flow per spec Appendix A, inside `asyncio.timeout(900)`: delete only the **video** sources (reference sources stay), `add_url(..., wait=True)`, `chat.ask`, return `answer`.
  - `async def ping(self) -> None`: `notebooks.list()` inside `asyncio.timeout(60)`.
  - Error mapping, shared by both methods, in this order:
    - `SourceError` → `VideoNotReadyError`
    - `AuthError`, `HeadlessReauthError` or `FileNotFoundError` on the storage file → `NotebookAuthError`
    - any other `NotebookLMError` or `TimeoutError` → `SummarizerError`

- [ ] **Step 1: Failing test** `test_send_posts_html_payload`. Use a MockTransport that records the request. Assert the URL ends with `/bot<token>/sendMessage`, and the JSON has `parse_mode == "HTML"` and `disable_web_page_preview is True`. Assert the result is `True`. Implement.
- [ ] **Step 2: Failing tests** `test_send_returns_false_on_non_200` and `test_send_returns_false_on_transport_error` (the transport raises `httpx.ConnectError`; assert with `caplog` that the token does not appear in any log record). Implement.
- [ ] **Step 3: Write a fake NotebookLM client** in `tests/test_summarizer.py`. It has a `sources` object (`list`, `delete`, `add_url`), a `notebooks` object (`list`) and a `chat` object (`ask` returning an object with `.answer`), and it records calls. The factory returns an async context manager yielding it. Write the failing test `test_summarize_replaces_sources_and_returns_answer`: it asserts that every pre-existing video source (and no other) was deleted before `add_url` and that `answer` is returned. Implement.
- [ ] **Step 4: Failing test** `test_ping_lists_notebooks`. Implement.
- [ ] **Step 5: Parametrized failing test** `test_summarizer_maps_library_errors`. Raise each library exception (and `TimeoutError`) from `add_url` and from `notebooks.list`, and assert the mapped error. Construct the library exceptions with the minimal constructor 0.8.6 accepts. Implement.
- [ ] **Step 6: NotebookLM account and notebook (owner + agent, no commit).** The service uses a **new Google account**, not the one of the previous system.
  1. `NOTEBOOKLM_PROFILE=youtube-summary notebooklm login` with the new account (a browser opens). Check it with `notebooklm auth check --test --json`.
  2. `notebooklm create "<title>" --json` (same profile), and note the id in the untracked `local/config/config.yml`.
  3. Configure the notebook's chat once with `notebooklm configure --notebook <id> --persona "<persona>" --response-length <default|shorter|longer>`. The persona holds the stable instructions: role, language, tone, what to ignore (sponsors, greetings), no citations. It can be up to 10,000 chars. It lives in the Google account, never in the repo. The `prompt` in `config.yml` stays the short per-video ask with the JSON contract.
- [ ] **Step 7: Live smoke check (manual, not in CI).** Write `scripts/smoke_notebooklm.py URL [--prompt-file FILE ...]`. It reads the env and `config.yml`, runs `ping()` and prints the `storage_state.json` mtime before and after. Then it adds the video **once** and asks every given prompt file (default: the `config.yml` prompt) against that same source. For each it prints the raw answer, `parse_answer` of it, the summary length and whether the JSON parsed. Prompt files live under `local/` (git-ignored).
  - Run it once against the new notebook. Expected: the mtime changes after `ping()`, and the answer is JSON-shaped Spanish.
  - If the mtime does not change, pass `keepalive=` to `from_storage` and re-check.
  - If 0.8.6 imports or signatures differ from the spec, fix the adapter.
  - Record any difference in the README's troubleshooting section.
- [ ] **Step 8: Persona and prompt tuning (owner + agent, no commit).**
  1. Baseline: the owner's previous analyzer prompt, adapted to the JSON contract.
  2. Pick 4–5 recent videos from `channels.yml` covering the variety: long and short, interview and monologue, one off-topic for the filter.
  3. Score every answer on:
     - valid JSON on the first try;
     - Spanish;
     - summary ≤ 1500 chars and self-contained;
     - 3–6 key points that are concrete (figures, assets, dates) rather than generic;
     - no citation markers;
     - faithful to the video, with nothing invented.
  4. Change one thing at a time: the persona via `configure`, or the prompt file. Re-run on the same videos.
  5. **Budget:** each ask is one chat query on a limited plan. Plan ≤ 25 asks for the whole tuning and print the running count.
  6. The winning prompt goes to the untracked `config.yml` and the owner's private copy. The persona stays in the notebook. Write both in the private runbook as a backup.
- [ ] **Step 9: Validate and commit** `feat: telegram client and notebooklm summarizer` (code and `scripts/smoke_notebooklm.py` only, no prompt).

---

### Task 7: Iteration orchestration

**Files:**
- Create: `src/youtube_summary/cycle.py`
- Test: `tests/test_cycle.py`, shared builders in `tests/conftest.py` (`make_video(...)`, `make_config(...)`, `FakeSummarizer` (records `summarize` and `ping` calls), `FakeSender` (scripted results), `fake_fetch(mapping)`)

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces:
  - `class Summarizer(Protocol): async def summarize(self, video_url: str, prompt: str) -> str; async def ping(self) -> None`
  - `class Sender(Protocol): async def send(self, text: str) -> bool`
  - `FetchVideos = Callable[[Channel], Awaitable[list[Video]]]`
  - `@dataclass(frozen=True) class CycleDeps: config: AppConfig; channels: tuple[Channel, ...]; state_path: Path; fetch: FetchVideos; summarizer: Summarizer; sender: Sender; stop: asyncio.Event`
  - `@dataclass class CycleResult: bootstrapped: bool = False; sent: int = 0; retried: int = 0; deferred: int = 0; failed: int = 0; given_up: int = 0; feed_errors: int = 0; auth_failed: bool = False`
  - `async def run_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult`
- Algorithm: spec "Behaviour of one iteration", steps 1–8, the send rule and the auth-alert path. Keep each helper ≤ 20 lines: `_bootstrap`, `_keepalive`, `_retry_pending`, `_collect_candidates` (fetch, `FeedError` → log + `feed_errors += 1`, filter, sort by `published_at`), `_process_video`, `_send_pending`, `_handle_auth_failure`. `deps.stop.is_set()` is checked before each video and each pending send.

- [ ] **Step 1: Failing test** `test_first_run_bootstraps_without_sending`. No state file and 2 feed videos: `sent == 0`, `bootstrapped`, the state file exists, and both ids are seen. Implement.
- [ ] **Step 2: Failing test** `test_new_video_is_summarised_sent_and_marked_seen`. Assert pending is empty afterwards. Implement.
- [ ] **Step 3: Failing test** `test_keepalive_runs_every_iteration_even_outside_hours` (Review Focus 6). With `within_hours=False`: `ping` called once, `summarize` never, no send. Implement.
- [ ] **Step 4: Failing test** `test_keepalive_auth_failure_alerts_once`. `ping` raises `NotebookAuthError`: one alert, no fetch, `auth_failed`. Run again: no second alert. Then a run with a working `ping` clears `auth_alert_sent`. Implement.
- [ ] **Step 5: Failing test** `test_keepalive_other_error_does_not_stop_iteration`. `ping` raises `SummarizerError`; the new video is still sent. Implement.
- [ ] **Step 6: Failing test** `test_skips_seen_pending_old_and_filtered_videos`. Four videos: one already seen, one pending, one 49 h old, one whose title misses the filter. Summarizer calls == 0. Implement.
- [ ] **Step 7: Failing test** `test_processes_videos_oldest_first`. Implement.
- [ ] **Step 8: Failing test** `test_video_not_ready_stays_unseen`. Assert `deferred == 1`, the id is not seen and has no failure count. Implement.
- [ ] **Step 9: Failing test** `test_summarize_auth_failure_stops_iteration`. Two candidates and a summarizer raising `NotebookAuthError`: one alert, the summarizer is called once, `auth_failed`. Implement.
- [ ] **Step 10: Failing test** `test_summarizer_error_gives_up_after_three_failures`. First run: `failed == 1, sent == 1` (the next video still goes out) and the failing id is not seen. After the third failing run the id is seen and `given_up == 1`; a fourth run does not call `summarize` for it. Implement.
- [ ] **Step 11: Failing test** `test_failed_feed_does_not_block_other_channels` (Review Focus 4). Implement.
- [ ] **Step 12: Failing test** `test_telegram_failure_keeps_summary_and_never_resummarises` (Review Focus 5). Use a long summary forcing 2 chunks and a sender scripted `[True, False]`. After run 1: the id is pending with only the second chunk and `send_attempts == 1`, and the state file on disk already holds it. Run 2 with sender `[False]`; run 3 with sender `[False]`: the id is seen, pending is empty, `given_up == 1`. Across all runs `summarize` was called exactly once and the first chunk was sent exactly once. Implement.
- [ ] **Step 13: Failing test** `test_pending_is_retried_and_sent`. Pending from a previous run plus sender `[True]` → `retried == 1`, seen, no `summarize` call. Implement.
- [ ] **Step 14: Failing test** `test_stop_event_prevents_new_videos`. `stop` is set before the call: `ping` may run, but no `summarize` or send happens, and the state is saved. Implement.
- [ ] **Step 15: Failing test** `test_cycle_prunes_old_seen_entries` (an entry older than 72 h is gone after the iteration). Implement.
- [ ] **Step 16: Validate and commit** `feat: keepalive, summarise and send iteration`.

---

### Task 8: Main loop and entry point

**Files:**
- Modify: `src/youtube_summary/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `run_cycle`, `CycleDeps`, loaders, adapters.
- Produces:
  - `def is_within_active_hours(now: datetime, hours: ActiveHours) -> bool` (local hour in `hours.timezone`, inclusive both ends)
  - `async def run_forever(deps: CycleDeps, *, clock: Callable[[], datetime], sleep: Callable[[float], Awaitable[None]], max_iterations: int | None = None) -> None`. Each iteration: `run_cycle(deps, now, within_hours=is_within_active_hours(...))` and log the `CycleResult`; any unexpected exception is logged with traceback and the loop continues. Then `sleep(poll_interval_minutes * 60)`. Return as soon as `deps.stop` is set (checked after the cycle and after the sleep).
  - `async def interruptible_sleep(stop: asyncio.Event, seconds: float) -> None`: returns after `seconds` or as soon as `stop` is set.
  - `def build_deps(settings: Settings, http: httpx.AsyncClient, stop: asyncio.Event) -> CycleDeps`: loads `config.yml` and `channels.yml` from `settings.config_dir`. The state path is `settings.state_dir / "seen.json"`.
  - `def main(argv: list[str] | None = None) -> None`. `argparse` takes `--once` (one iteration with `within_hours=True`, then exit). Logging: `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")`, then `httpx` and `httpcore` loggers at WARNING. Installs `loop.add_signal_handler` for SIGTERM and SIGINT that sets `stop`. A `ConfigError` exits with code 2 and the message on stderr.

- [ ] **Step 1: Parametrized failing test** `test_is_within_active_hours` with Madrid local times 06:59 → False, 07:00 → True, 23:59 → True, 00:30 → False (built as UTC datetimes that map to those local times). Implement.
- [ ] **Step 2: Failing test** `test_run_forever_passes_within_hours_and_sleeps_interval` (`max_iterations=1`, a clock outside hours; the fake cycle records `within_hours=False`; the fake sleep records `900`). Implement.
- [ ] **Step 3: Failing test** `test_run_forever_survives_cycle_exception`. With `max_iterations=2` and a first cycle raising `RuntimeError`, the second still runs. Implement.
- [ ] **Step 4: Failing tests** `test_run_forever_returns_when_stop_is_set` and `test_interruptible_sleep_returns_early_on_stop` (Review Focus 7; a 3600 s sleep returns well under 1 s after `stop.set()`). Implement.
- [ ] **Step 5: Failing test** `test_main_exits_2_on_config_error`. Use an env missing `TELEGRAM_BOT_TOKEN` via `monkeypatch`, and `pytest.raises(SystemExit)` with code 2. Implement.
- [ ] **Step 6: Failing test** `test_main_silences_httpx_info_logs`: after logging setup, `logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING`. Implement.
- [ ] **Step 7: Manual check.** Point `CONFIG_DIR`/`STATE_DIR`/`NOTEBOOKLM_STORAGE_STATE_PATH` at a `local/` dir holding real config and run `uv run youtube-summary --once` twice. Expected: the first run logs bootstrap; the second logs a keepalive and `sent=0` unless a new video appeared.
- [ ] **Step 8: Validate and commit** `feat: scheduling loop, graceful stop and cli entry point`.

---

### Task 9: Container and deployment tooling

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/server.env.sample`, `scripts/ops/post-receive`, `scripts/notebooklm_session/sync_session.sh`, `.claude/skills/deploy/SKILL.md`, `.claude/skills/renew-notebooklm-session/SKILL.md`
- Test: CI image build (Task 1 job); `tests/test_post_receive.py`; `tests/test_repo_hygiene.py`

- [ ] **Step 1: `Dockerfile`.** Base `ghcr.io/astral-sh/uv:python3.15-trixie-slim@sha256:<digest>`; there is no bookworm tag for 3.15. Get the digest with `docker buildx imagetools inspect`; Dependabot keeps it current. Set `ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy`. Copy `pyproject.toml uv.lock .python-version`, run `uv sync --frozen --no-dev --no-install-project`, copy `src/`, run `uv sync --frozen --no-dev --no-editable`. Set `ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOME=/tmp` and `CMD ["youtube-summary"]`. `.dockerignore` excludes `.git`, `.venv`, `tests`, `local`, `state`, `secrets`, `.env`, `config.yml`, `channels.yml`, `scripts`.
- [ ] **Step 2: `docker-compose.yml`:**

```yaml
name: youtube-summary-telegram
services:
  youtube-summary:
    build: .
    restart: unless-stopped
    user: "${YST_UID:-1000}:${YST_GID:-1000}"
    stop_grace_period: 2m
    env_file: ${YST_HOME:?set YST_HOME}/config/.env
    environment:
      CONFIG_DIR: /config
      STATE_DIR: /state
      NOTEBOOKLM_STORAGE_STATE_PATH: /secrets/notebooklm/storage_state.json
    volumes:
      - ${YST_HOME}/config:/config:ro
      - ${YST_HOME}/state:/state
      - ${YST_HOME}/secrets/notebooklm:/secrets/notebooklm
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
```

- [ ] **Step 3: Failing test** `tests/test_post_receive.py`. Run `scripts/ops/post-receive` with `bash`, `POST_RECEIVE_DRY_RUN=1`, a `tmp_path` `YST_HOME`, and refs on stdin. Strip `GIT_*` variables from the subprocess env. Cases:
  - an update of `refs/heads/main` prints `[DRY RUN] docker compose ... up -d --build` and the project-scoped `docker image prune`;
  - `refs/heads/other` prints `No deploy` and runs nothing;
  - a deletion of `refs/heads/main` (`newrev` all zeros) prints `No deploy`;
  - `POST_RECEIVE_FORCE=1` with empty stdin deploys.
- [ ] **Step 4: `scripts/ops/post-receive`.** Implement the behaviour in spec Appendix A "Deploy hook behaviour" until the test passes. The header comment shows the install commands with placeholders (`<ssh-target>`, `<YST_HOME>`).
- [ ] **Step 5: `scripts/server.env.sample`:** `SERVER_SSH_TARGET=<user>@<host>`, `SERVER_YST_HOME=<absolute YST_HOME on the server>`. The real `scripts/server.env` is git-ignored. It is the single source of server coordinates for the scripts and both skills.
- [ ] **Step 6: `scripts/notebooklm_session/sync_session.sh`** (bash, `set -euo pipefail`):
  1. Source `../server.env` relative to the script; fail with a clear message if it is missing. Require `SERVER_SSH_TARGET`, `SERVER_YST_HOME`.
  2. `export NOTEBOOKLM_PROFILE="${NOTEBOOKLM_PROFILE:-youtube-summary}"`, the service's own desktop profile.
  3. Require `notebooklm`, `jq`, `rsync`.
  4. If `--force` is given, or `notebooklm auth check --test --json | jq -e '.status == "ok"'` fails, run `notebooklm login` and re-check; exit 1 if it still fails.
  5. `ssh "$SERVER_SSH_TARGET" "install -d -m 700 '$SERVER_YST_HOME/secrets/notebooklm'"`, then rsync `~/.notebooklm/profiles/$NOTEBOOKLM_PROFILE/storage_state.json` to `$SERVER_SSH_TARGET:$SERVER_YST_HOME/secrets/notebooklm/storage_state.json` with `--chmod=u=rw,go-rwx`.
- [ ] **Step 7: Skills.** Both tell the agent to read real values from `scripts/server.env` (untracked) and never to write them into the repo. Remote commands use double quotes so that variables expand **locally**.
  - `deploy/SKILL.md` (frontmatter `name`, `description`). Steps:
    1. Run validation.
    2. `git push origin main`.
    3. `git push production main`.
    4. `ssh "$SERVER_SSH_TARGET" "cd '$SERVER_YST_HOME/app' && docker compose --env-file '$SERVER_YST_HOME/config/.env' logs --tail 50"`.
    5. Confirm an iteration log line with a keepalive and no auth error.
    6. Rollback: `git push production <previous-sha>:main --force`.
  - `renew-notebooklm-session/SKILL.md`. Steps:
    1. Run `scripts/notebooklm_session/sync_session.sh --force`.
    2. Restart is not needed; the next iteration picks the session up.
    3. Check the logs for a successful keepalive and no auth error.
- [ ] **Step 8: Failing test** `tests/test_repo_hygiene.py::test_no_secrets_or_private_values_committed`. It runs `git ls-files`, reads every tracked text file and asserts none matches:
  - a Telegram bot token (`\d{8,10}:[A-Za-z0-9_-]{35}`);
  - a negative chat id (`-100\d{6,}`);
  - an `@[\w.-]+\.(local|ts\.net)` host;
  - the previous system's name. Build the pattern from pieces (`"tr" + "ai" + "dingbros"`, case-insensitive) so the test file does not match itself.

  The test strips `GIT_*` variables from the subprocess env. Make it pass (it should already), then keep it.
- [ ] **Step 9: Local container check.** Copy your desktop session to `local/secrets/notebooklm/storage_state.json`, put real config in `local/config/`, then run `YST_HOME=$PWD/local docker compose --env-file local/config/.env up --build`. Expected: the bootstrap log line, then the sleep. Then `docker compose stop` returns in a few seconds (graceful SIGTERM), and every file under `local/` is owned by your user.
- [ ] **Step 10: Validate and commit** `feat: docker image, compose, deploy hook and session sync`.

---

### Task 10: Dependency bot

**Files:**
- Create: `.github/dependabot.yml`, `.github/workflows/dependabot-automerge.yml`

- [ ] **Step 1: `.github/dependabot.yml`.** `version: 2`. One entry per ecosystem, each with a 3-day `cooldown` and `directory: "/"`, `schedule: {interval: weekly}` and a commit-message prefix (`chore(deps)`, `ci(deps)` for actions):
  - `uv`. Group `python-minor-patch` with `patterns: ["*"]`, `exclude-patterns: ["notebooklm-py"]` and `update-types: ["minor", "patch"]`. `notebooklm-py` gets its own PRs.
  - `docker` (the Dockerfile base image digest).
  - `github-actions`, with all updates grouped.
  - `pre-commit`, with all updates grouped.
- [ ] **Step 2: `.github/workflows/dependabot-automerge.yml`.** Trigger `pull_request`; set `permissions: contents: write, pull-requests: write`; `if: github.actor == 'dependabot[bot]'`. Steps:
  1. `dependabot/fetch-metadata` (latest major).
  2. Auto-merge only Python patch updates, `github-actions`/`pre-commit` minor and patch updates, and `docker` digest-only updates; `notebooklm-py`, Python minors, Docker tag changes and majors stay manual. Merge with `gh pr merge --auto --squash "$PR_URL"` with `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`.
  3. Otherwise do nothing; the PR waits for the owner.
- [ ] **Step 3: Owner-only GitHub settings** (documented in the README, not code):
  - Settings → General → "Allow auto-merge" on.
  - A ruleset on `main` that requires the status checks `test` and `image` from CI, with the repository admin as bypass actor so direct pushes keep working.

  Without the required checks, `--auto` would merge at once without waiting for CI.
- [ ] **Step 4: Validate and commit** `ci: dependabot with auto-merge for safe updates`.

---

### Task 11: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README** for a public reader. Sections:
  - What it does: a one-paragraph description and a sample Telegram message in a code block.
  - How it works: the iteration in short bullets, including bootstrap, dedup, keepalive, the pending-message retry (3 attempts, never re-summarised) and the 3-failure give-up.
  - Requirements: Docker, a dedicated Telegram bot (BotFather) added to the group, a Google account with NotebookLM.
  - Setup:
    1. Create a dedicated NotebookLM notebook; the id is the last URL path segment.
    2. Fill `scripts/server.env` and run `scripts/notebooklm_session/sync_session.sh --force`. It logs in with the `youtube-summary` profile and copies the session to the server.
    3. Find a channel id: the `channel_id` in the page source, or `https://www.youtube.com/@handle` → "Share channel" → "Copy channel ID".
    4. Get a group chat id (`getUpdates`).
  - Configuration reference: every key of `.env`, `config.yml`, `channels.yml` and `scripts/server.env`, with type, default and example. Changing `config.yml` or `channels.yml` needs `docker compose restart`.
  - Run locally: with uv (`--once`) and with Docker.
  - Deploy to your own server:
    - bare repo + `post-receive`;
    - the directory layout `$YST_HOME/{repo.git,app,config,state,secrets/notebooklm}`, owned by the server user (uid `YST_UID`, default 1000);
    - `git remote add production ...`, `git push production main`;
    - rollback.
  - Renewing the NotebookLM session.
  - Operations: logs, resetting state (delete `seen.json` → bootstrap; no `sudo` needed), adding a channel (edit + restart), what "given up" lines mean.
  - Dependency updates: Dependabot, what is auto-merged, why `notebooklm-py` is manual (run `scripts/smoke_notebooklm.py`), the GitHub settings from Task 10 Step 3.
  - Troubleshooting: auth alert, video not ready, Telegram 400 (HTML parse error), any 0.8.6 API or keepalive differences found in Task 6.
  - Development: commands, TDD, CI.
  - License: Apache 2.0.

  Placeholders only.
- [ ] **Step 2: Check** every command in the README against the real files (paths, env names, flags).
- [ ] **Step 3: Validate and commit** `docs: README`.

---

### Task 12: Rollout (owner, on the real server)

Done by the owner or by an agent with the owner's SSH access. Real values come from the owner's private runbook and are never written to this repo. Nothing here touches the previous system's files.

- [ ] **Step 1:** Use the notebook, Google account and desktop profile set up in Task 6 Steps 6–8. Do not create another one.
- [ ] **Step 2:** Create a dedicated Telegram bot with BotFather and add it to the group. The chat id is the group the previous system posted to.
- [ ] **Step 3:** On the server: `mkdir -p $YST_HOME/{app,config,state,secrets/notebooklm}`, `chmod 700 $YST_HOME/secrets`, `git init --bare $YST_HOME/repo.git`, install `scripts/ops/post-receive` as `$YST_HOME/repo.git/hooks/post-receive` (`chmod +x`). Check that `id -u`/`id -g` match `YST_UID`/`YST_GID` (default 1000), or set them in `.env`.
- [ ] **Step 4:** Write `$YST_HOME/config/.env`, `config.yml` and `channels.yml` (`chmod 600 .env`).
- [ ] **Step 5:** On the desktop: fill `scripts/server.env`, run `scripts/notebooklm_session/sync_session.sh --force` and confirm `storage_state.json` exists on the server.
- [ ] **Step 6:** From the desktop: `git remote add production <ssh-target>:<YST_HOME>/repo.git && git push production main`.
- [ ] **Step 7:** Check the logs for the bootstrap line (`bootstrapped=True`) and confirm `seen.json` exists. After the next iteration, confirm a successful keepalive and that `storage_state.json`'s mtime changed.
- [ ] **Step 8:** Right away, deploy the previous system's already-committed "stop YouTube schedules" change (private repo; spec "Decommissioning the previous system"). If it was deployed before Step 6, videos from that gap are not sent (accepted).
- [ ] **Step 9:** Apply the GitHub settings from Task 10 Step 3.
- [ ] **Step 10:** Wait for the first new video from a configured channel. Confirm it arrives in the Telegram group exactly once, formatted correctly.

---

## Merge requests

- **Count: 0 PRs from the builder. Commits go directly to `main`,** one per task (Tasks 1–11). The repo is new and single-owner; the owner works on `main` without feature branches. CI runs on every push, so a red CI after a task push is fixed before the next task.
- **If the owner prefers review:** 1 PR, `feat/initial-service` → `main`, containing Tasks 1–11, merged once CI is green. Splitting further adds no review value, because every task depends on the previous one.
- **After rollout:** Dependabot opens weekly PRs; safe ones auto-merge once CI is green (Task 10).
- **Task 12** is operational and produces no commit in this repo. The decommissioning lands in the owner's private repo (already committed), deployed at Step 8.
- **Dependencies:** each task depends on the previous ones. Task 12 needs Tasks 1–11 on `main`.

## Self-review notes

- Every spec section maps to tasks:
  - runtime (Python 3.15, non-root, compose name, SIGTERM, log level) → 1, 8, 9
  - config → 2; feed + filter → 3; state with pending and failures → 4
  - HTML message format and split → 5; adapters with keepalive and timeouts → 6
  - iteration steps 1–8, send rule and auth path → 7; active hours + CLI + stop → 8
  - deploy hook, session and independence → 9; dependency bot → 10
  - README → 11; CLAUDE.md + CI → 1; skills → 9; rollout + decommission → 12
- The Review Focus items are pinned in Tasks 4, 5, 7 and 8.
- Names used across tasks: `Video`, `Channel`, `AppConfig`, `ActiveHours`, `Settings`, `Summary`, `SeenState`, `PendingMessage`, `CycleDeps`, `CycleResult`, `run_cycle`, `NotebookLMSummarizer`, `TelegramClient`.
