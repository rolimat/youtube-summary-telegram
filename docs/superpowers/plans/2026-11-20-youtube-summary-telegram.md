# youtube-summary-telegram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-container Python service that polls YouTube channel RSS feeds, summarises new videos with NotebookLM and posts each summary to a Telegram group, with no database and no UI.

**Architecture:** One asyncio process (`youtube-summary`) loops: inside the active hours it runs one cycle, then sleeps. A cycle does four things. It reads the feeds, filters out seen, old and off-topic videos, summarises each remaining video and sends it to Telegram. It records handled video ids in a small, pruned `seen.json`. Pure parts (config, feed parsing, filtering, state, message formatting) are plain functions or classes. The three I/O edges (feed HTTP, NotebookLM, Telegram) are thin adapters behind `Protocol`s, so the cycle is unit-tested with fakes.

**Tech Stack:** Python 3.12, uv, httpx, PyYAML, tzdata, notebooklm-py 0.8.6; pytest, pytest-asyncio, pytest-cov, mypy (strict), ruff, pre-commit; Docker + docker compose; GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-11-20-youtube-summary-telegram-design.md`](../specs/2026-11-20-youtube-summary-telegram-design.md). Read it first. Its Appendix A holds the reference code to copy.

## Global Constraints

- Python `>=3.12`; package `youtube_summary` under `src/`; console script `youtube-summary = "youtube_summary.main:main"`.
- Runtime dependencies exactly: `httpx`, `pyyaml`, `tzdata`, `notebooklm-py==0.8.6`. Nothing else at runtime.
- Dev dependencies: `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-cov`, `mypy`, `types-PyYAML`, `ruff`, `pre-commit`.
- `mypy --strict` clean on `src/` and `tests/`. Ruff line length 100.
- Functions ≤ 20 lines, full type hints, no comments that restate code, OOP where state exists.
- TDD: one failing test first, then the code, for every behaviour.
- No test touches the network: feeds and Telegram use `httpx.MockTransport`; NotebookLM uses a fake client factory.
- Coverage gate: `--cov=youtube_summary --cov-fail-under=90`.
- No real token, chat id, notebook id, hostname, username or server path in any committed file. Samples use `<placeholders>`.
- Telegram: `parse_mode="MarkdownV2"`, `disable_web_page_preview=true`, 4096-char limit, 10 s timeout.
- Defaults in `config.yml.sample`: `poll_interval_minutes: 15`, `active_hours: {start: 7, end: 23, timezone: Europe/Madrid}`, `max_video_age_hours: 48`.
- `seen.json` pruning horizon: `max_video_age_hours + 24` hours.
- Commit after each task with Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`, `ci:`), run `pre-commit run --all-files` and the full test suite first.

## Review Focus

1. **Corrupt or empty `seen.json`** (disk full, manual edit): expected no crash loop and no flood. The file is renamed to `seen.json.corrupt-<UTC timestamp>` and the cycle bootstraps. Test is in Task 4.
2. **A single sentence longer than 4096 chars** (NotebookLM answer without punctuation): expected every chunk ≤ 4096. Test is in Task 5.
3. **Titles and summaries full of MarkdownV2 specials** (`S&P 500 (2025) -10%! [live]`): expected escaped text and a link whose URL contains `)`. Test is in Task 5.
4. **One channel feed fails** (deleted channel → 404, timeout, malformed XML): expected the other channels to still be processed and the failure logged. Test is in Task 7.
5. **Telegram rejects the second chunk of a split message**: expected the video to stay unseen. It is retried next cycle; a duplicate first chunk is accepted. Test is in Task 7.

---

## File Structure

```
src/youtube_summary/
  __init__.py
  __main__.py          # `python -m youtube_summary` → main()
  config.py            # Channel, ActiveHours, AppConfig, Settings, ConfigError, loaders
  feed.py              # Video, FeedError, parse_feed, fetch_channel_videos, matches_title_filter
  state.py             # SeenState (load/save/prune, auth flag)
  message.py           # Summary, parse_answer, format_message, format_auth_alert, escape_md, escape_url, split_for_telegram
  telegram.py          # TelegramClient
  summarizer.py        # NotebookLMSummarizer, VideoNotReadyError, NotebookAuthError, SummarizerError
  cycle.py             # CycleDeps, CycleResult, run_cycle
  main.py              # is_within_active_hours, run_forever, build_deps, main
tests/                 # test_<module>.py per module, conftest.py with shared builders
Dockerfile, docker-compose.yml, .env.sample, config.yml.sample, channels.yml.sample
scripts/ops/post-receive
scripts/notebooklm_session/sync_session.sh, scripts/notebooklm_session/.env.sample
.claude/skills/deploy/SKILL.md, .claude/skills/renew-notebooklm-session/SKILL.md
CLAUDE.md, README.md, .pre-commit-config.yaml, .github/workflows/ci.yml, .gitignore
```

---

### Task 1: Project skeleton, tooling, CI and CLAUDE.md

**Files:**
- Create: `pyproject.toml`, `uv.lock` (generated), `.python-version` (`3.12`), `.gitignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `CLAUDE.md`, `src/youtube_summary/__init__.py`, `src/youtube_summary/__main__.py`, `src/youtube_summary/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `youtube_summary.main.main() -> None` (temporary body: `raise SystemExit(0)`; replaced in Task 8).

- [ ] **Step 1: Write `pyproject.toml`.** Use the hatchling build backend with a src layout. Add the dependencies from Global Constraints and `[project.optional-dependencies] dev = [...]`. Set `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`. Set `[tool.ruff] line-length = 100`, `[tool.ruff.lint] select = ["E","F","I","UP","B","SIM","RUF"]`. Set `[tool.mypy] strict = true`, `mypy_path = "src"`.
- [ ] **Step 2: `uv lock && uv sync --extra dev`.** Expected: `.venv` created, `uv.lock` written.
- [ ] **Step 3: Write the failing test** `tests/test_main.py::test_main_module_is_importable`:

```python
def test_main_module_is_importable() -> None:
    from youtube_summary.main import main
    assert callable(main)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_main.py -q`. Expected: FAIL (ModuleNotFoundError).
- [ ] **Step 5: Create the package files** with the temporary `main()`. `__main__.py` calls `main()`.
- [ ] **Step 6: Run** the test again. Expected: PASS.
- [ ] **Step 7: `.gitignore`** must contain: `.venv/`, `__pycache__/`, `.coverage`, `.env`, `config.yml`, `channels.yml`, `local/`, `state/`, `seen.json*`, `storage_state.json`, `scripts/notebooklm_session/.env`.
- [ ] **Step 8: `.pre-commit-config.yaml`** with hooks `ruff` (with `--fix`), `ruff-format`, `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `detect-private-key`, and a local `mypy` hook running `uv run mypy src tests`.
- [ ] **Step 9: `.github/workflows/ci.yml`.** Trigger on push and pull_request to `main`; set `permissions: contents: read` and a concurrency group. Steps: `actions/checkout@v5`, `astral-sh/setup-uv@v6` (cache on), `uv python install 3.12`, `uv sync --frozen --extra dev`, `uv run pre-commit run --all-files`, `uv run pytest -q --cov=youtube_summary --cov-fail-under=90 --cov-report=term-missing:skip-covered`. Add a second job that builds the image: `docker build -t youtube-summary:ci .` (`needs:` the first job).
- [ ] **Step 10: `CLAUDE.md`.** Short sections:
  - **Core principles:** baby steps, TDD (one failing test at a time), full typing, small functions (≤ 20 lines), clear names, no comments, simplest solution, flag repeated code.
  - **Commands:** `uv sync --extra dev`, `uv run pytest -q`, `uv run mypy src tests`, `uv run pre-commit run --all-files`, `uv run youtube-summary --once`.
  - **Testing:** mock every external API (YouTube, NotebookLM, Telegram); `httpx.MockTransport` for HTTP; no network in tests.
  - **Security:** never commit `.env`, `config.yml`, `channels.yml`, `storage_state.json`, `seen.json`, tokens, chat ids, notebook ids, hostnames.
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
  - `@dataclass(frozen=True) class AppConfig: notebook_id: str; poll_interval_minutes: int; active_hours: ActiveHours; max_video_age_hours: int; prompt: str`
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
  - `config.yml.sample`: the defaults above plus a real default prompt in Spanish. It asks for a self-contained summary of at most 1500 characters plus 3–6 key points, and output ONLY the JSON object, with no markdown fences.
  - `channels.yml.sample`: two placeholder channels, one with `title_filter`.
  - `.env.sample`: `TELEGRAM_BOT_TOKEN=<bot-token>`, `TELEGRAM_CHAT_ID=<chat-id>`, `YST_HOME=<absolute dir holding config/ and state/ on the host>`, `NOTEBOOKLM_SESSION_DIR=<absolute host dir containing storage_state.json>`. Add commented `NOTEBOOKLM_STORAGE_STATE_PATH`, `CONFIG_DIR`, `STATE_DIR` lines for running without Docker.
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
  - `def matches_title_filter(title: str, words: tuple[str, ...]) -> bool`: empty `words` → True; otherwise a case-insensitive substring match of any word.

- [ ] **Step 1: Failing test** `test_parse_feed_returns_complete_entries`: the fixture yields 2 videos with the expected ids, titles, URLs and UTC datetimes.
- [ ] **Step 2: Run → FAIL; implement** `parse_feed` from spec Appendix A; **run → PASS.**
- [ ] **Step 3: Failing test** `test_parse_feed_raises_feed_error_on_malformed_xml`. Implement.
- [ ] **Step 4: Failing test** `test_fetch_channel_videos_raises_on_404`. Build `httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))` and expect `FeedError` with `match="Example"`. Then `test_fetch_channel_videos_requests_channel_feed_url`: assert that the requested URL equals `FEED_URL_TEMPLATE.format(channel_id=...)`. Implement.
- [ ] **Step 5: Parametrized failing test** `test_matches_title_filter` with cases:
  - `("Hablemos de BOLSA", ("bolsa",), True)`
  - `("Vlog", ("bolsa",), False)`
  - `("Vlog", (), True)`
  - `("Inflación y tipos", ("inflación", "fed"), True)`

  Implement.
- [ ] **Step 6: Validate and commit** `feat: youtube rss feed parsing and title filter`.

---

### Task 4: Seen state

**Files:**
- Create: `src/youtube_summary/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces `class SeenState`:
  - `@classmethod load(cls, path: Path) -> SeenState | None`: returns `None` when the file does not exist (the caller bootstraps). When the file is empty or not valid JSON: log an error, rename it to `seen.json.corrupt-<YYYYmmddTHHMMSSZ>` in the same directory, return `None`.
  - `@classmethod empty(cls, path: Path) -> SeenState`
  - `is_seen(self, video_id: str) -> bool`
  - `mark_seen(self, video_id: str, at: datetime) -> None`
  - `prune(self, now: datetime, horizon: timedelta) -> None`: removes entries with `at < now - horizon`.
  - `auth_alert_sent: bool` (read/write attribute)
  - `save(self) -> None`: atomic. Write `<path>.tmp` in the same dir, then `os.replace`. Create the parent dir if missing.
- File format: `{"version": 1, "seen": {"<video_id>": "<ISO-8601 UTC>"}, "auth_alert_sent": false}`.

- [ ] **Step 1: Failing test** `test_load_returns_none_when_file_missing`. Implement. Run.
- [ ] **Step 2: Failing test** `test_save_then_load_round_trips`. Mark 2 ids, set `auth_alert_sent = True`, save, load; assert both ids are seen and the flag is True. Implement.
- [ ] **Step 3: Failing test** `test_prune_drops_entries_older_than_horizon`. One id is 73 h old and one 1 h old, with horizon 72 h; only the recent one stays. Implement.
- [ ] **Step 4: Failing test** `test_load_quarantines_corrupt_file` (Review Focus 1). Write `"{not json"`; assert that `load` returns `None`, the original path is gone, and exactly one `seen.json.corrupt-*` file exists. Add `test_load_quarantines_empty_file` (0 bytes). Implement.
- [ ] **Step 5: Failing test** `test_save_leaves_no_tmp_file`. Implement.
- [ ] **Step 6: Validate and commit** `feat: pruned atomic seen-video state`.

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
  - `def escape_md(text: str) -> str`, `def escape_url(url: str) -> str`: copied from spec Appendix A.
  - `def format_message(video: Video, summary: Summary) -> str`: the layout from the spec. Header `🎬 *{channel}* — {title}`; an empty title shows `Sin título`. Key-point lines are `• {point}`, and the key-points block is omitted when empty. The final line is `🔗 [Ver vídeo]({escape_url(url)})`. Blocks are joined by `"\n\n"`.
  - `def split_for_telegram(text: str, max_chars: int = 4096) -> list[str]`: spec Appendix A plus a hard cut of any chunk still longer than `max_chars`.
  - `def format_auth_alert() -> str`: the MarkdownV2-escaped Spanish text `⚠️ La sesión de NotebookLM ha caducado. Renueva storage_state.json (ver README). Los vídeos pendientes se enviarán al renovarla.`

- [ ] **Step 1: Failing test** `test_parse_answer_reads_json`. Implement. Run.
- [ ] **Step 2: Failing test** `test_parse_answer_strips_fences_and_citations`. Input: ```` ```json\n{"summary": "Sube [1].", "key_points": ["A [2, 3]"]}\n``` ````. Expected: `Summary("Sube.", ("A",))`. Implement.
- [ ] **Step 3: Failing test** `test_parse_answer_falls_back_to_raw_text` (`"Texto libre [1]"` → `Summary("Texto libre", ())`). Implement.
- [ ] **Step 4: Failing test** `test_format_message_escapes_markdown_specials` (Review Focus 3). Use title `S&P 500 (2025) -10%! [live]`, a summary with `.`, and a URL containing `)`. Assert the header contains `S&P 500 \\(2025\\) \\-10%\\! \\[live\\]` and the link ends with `\\))`. Implement.
- [ ] **Step 5: Failing test** `test_format_message_omits_empty_key_points` (no `•` in the output). Implement.
- [ ] **Step 6: Failing test** `test_split_for_telegram_hard_cuts_oversized_sentence` (Review Focus 2): `"a" * 9000` → 3 chunks, all ≤ 4096, joined back equals the input. Add `test_split_for_telegram_splits_on_sentences`. Implement.
- [ ] **Step 7: Validate and commit** `feat: telegram message formatting`.

---

### Task 6: Telegram client and NotebookLM summarizer

**Files:**
- Create: `src/youtube_summary/telegram.py`, `src/youtube_summary/summarizer.py`
- Test: `tests/test_telegram.py`, `tests/test_summarizer.py`

**Interfaces:**
- Produces:
  - `class TelegramClient: __init__(self, bot_token: str, chat_id: str, http: httpx.AsyncClient)`; `async def send(self, text: str) -> bool` (spec Appendix A "Telegram send"; never raises).
  - `class VideoNotReadyError(Exception)`, `class NotebookAuthError(Exception)`, `class SummarizerError(Exception)`
  - `class NotebookLMSummarizer: __init__(self, storage_state_path: Path, notebook_id: str, client_factory: Callable[[str], AbstractAsyncContextManager[Any]] | None = None)`. The default factory is `NotebookLMClient.from_storage`.
  - `async def summarize(self, video_url: str, prompt: str) -> str`. Flow per spec Appendix A: delete **all** sources, `add_url(..., wait=True)`, `chat.ask`, return `answer`. Error mapping:
    - `SourceError` → `VideoNotReadyError`
    - `notebooklm.exceptions.AuthError`, `notebooklm.exceptions.HeadlessReauthError` or `FileNotFoundError` on the storage file → `NotebookAuthError`
    - any other `NotebookLMError` → `SummarizerError`

- [ ] **Step 1: Failing test** `test_send_posts_markdown_v2_payload`. Use a MockTransport that records the request. Assert the URL ends with `/bot<token>/sendMessage`, and the JSON has `parse_mode == "MarkdownV2"` and `disable_web_page_preview is True`. Assert the result is `True`. Implement.
- [ ] **Step 2: Failing tests** `test_send_returns_false_on_non_200` and `test_send_returns_false_on_transport_error` (the transport raises `httpx.ConnectError`). Implement.
- [ ] **Step 3: Write a fake NotebookLM client** in `tests/test_summarizer.py`. It has a `sources` object (`list`, `delete`, `add_url`) and a `chat` object (`ask` returning an object with `.answer`), and it records calls. The factory returns an async context manager yielding it. Write the failing test `test_summarize_replaces_sources_and_returns_answer`: it asserts that every pre-existing source was deleted before `add_url` and that `answer` is returned. Implement.
- [ ] **Step 4: Parametrized failing test** `test_summarize_maps_library_errors`. Raise each library exception from `add_url` and assert the mapped error. Construct the library exceptions with the minimal constructor 0.8.6 accepts. Implement.
- [ ] **Step 5: Live smoke check (manual, not in CI).** Add `scripts/smoke_notebooklm.py`. It loads `.env` values from the environment and `config.yml`, summarises one public video URL given on argv, and prints the answer and `parse_answer` of it. Run it once against the real notebook. Expected: a JSON-shaped Spanish answer. If 0.8.6 imports or signatures differ from the spec, fix the adapter and note the difference in the README's troubleshooting section.
- [ ] **Step 6: Validate and commit** `feat: telegram client and notebooklm summarizer`.

---

### Task 7: Cycle orchestration

**Files:**
- Create: `src/youtube_summary/cycle.py`
- Test: `tests/test_cycle.py`, shared builders in `tests/conftest.py` (`make_video(...)`, `make_config(...)`, `FakeSummarizer`, `FakeSender`, `fake_fetch(mapping)`)

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces:
  - `class Summarizer(Protocol): async def summarize(self, video_url: str, prompt: str) -> str`
  - `class Sender(Protocol): async def send(self, text: str) -> bool`
  - `FetchVideos = Callable[[Channel], Awaitable[list[Video]]]`
  - `@dataclass(frozen=True) class CycleDeps: config: AppConfig; channels: tuple[Channel, ...]; state_path: Path; fetch: FetchVideos; summarizer: Summarizer; sender: Sender`
  - `@dataclass class CycleResult: bootstrapped: bool = False; sent: int = 0; deferred: int = 0; failed: int = 0; feed_errors: int = 0; auth_failed: bool = False`
  - `async def run_cycle(deps: CycleDeps, now: datetime) -> CycleResult`
- Algorithm: spec "Behaviour of one cycle", steps 1–6. Keep each helper ≤ 20 lines: `_bootstrap`, `_collect_candidates` (fetch, `FeedError` → log + `feed_errors += 1`, filter, sort by `published_at`), `_process_video`, `_send_chunks`, `_handle_auth_failure`.

- [ ] **Step 1: Failing test** `test_first_run_bootstraps_without_sending`. No state file and 2 feed videos: `sent == 0`, `bootstrapped`, the state file exists, and both ids are seen. Implement.
- [ ] **Step 2: Failing test** `test_new_video_is_summarised_sent_and_marked_seen`. Implement.
- [ ] **Step 3: Failing test** `test_skips_seen_old_and_filtered_videos`. Three videos: one already seen, one 49 h old, one whose title misses the filter. Summarizer calls == 0. Implement.
- [ ] **Step 4: Failing test** `test_processes_videos_oldest_first`. Implement.
- [ ] **Step 5: Failing test** `test_video_not_ready_stays_unseen`. Assert `deferred == 1` and the id is not seen. Implement.
- [ ] **Step 6: Failing test** `test_auth_failure_alerts_once_and_stops_cycle`. Two candidates and a summarizer raising `NotebookAuthError`: one alert is sent, the summarizer is called once, and `auth_failed`. Run again: no second alert. Then a successful run clears `auth_alert_sent`. Implement.
- [ ] **Step 7: Failing test** `test_other_summarizer_error_continues_with_next_video`. Assert `failed == 1, sent == 1`. Implement.
- [ ] **Step 8: Failing test** `test_failed_feed_does_not_block_other_channels` (Review Focus 4). Implement.
- [ ] **Step 9: Failing test** `test_partial_telegram_failure_keeps_video_unseen` (Review Focus 5). Use a long summary forcing 2 chunks and a sender returning `[True, False]`. Implement.
- [ ] **Step 10: Failing test** `test_cycle_prunes_old_seen_entries` (an entry older than 72 h is gone after the cycle). Implement.
- [ ] **Step 11: Validate and commit** `feat: summarise-and-send cycle`.

---

### Task 8: Main loop and entry point

**Files:**
- Modify: `src/youtube_summary/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `run_cycle`, `CycleDeps`, loaders, adapters.
- Produces:
  - `def is_within_active_hours(now: datetime, hours: ActiveHours) -> bool` (local hour in `hours.timezone`, inclusive both ends)
  - `async def run_forever(deps: CycleDeps, *, clock: Callable[[], datetime], sleep: Callable[[float], Awaitable[None]], max_iterations: int | None = None) -> None`. Each iteration: if within hours, `run_cycle` and log the `CycleResult`; any unexpected exception is logged with traceback and the loop continues. Then `sleep(poll_interval_minutes * 60)`.
  - `def build_deps(settings: Settings, http: httpx.AsyncClient) -> CycleDeps`: loads `config.yml` and `channels.yml` from `settings.config_dir`. The state path is `settings.state_dir / "seen.json"`.
  - `def main(argv: list[str] | None = None) -> None`. `argparse` takes `--once` (run one cycle regardless of hours, then exit). Logging: `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")`. A `ConfigError` exits with code 2 and the message on stderr.

- [ ] **Step 1: Parametrized failing test** `test_is_within_active_hours` with Madrid local times 06:59 → False, 07:00 → True, 23:59 → True, 00:30 → False (built as UTC datetimes that map to those local times). Implement.
- [ ] **Step 2: Failing test** `test_run_forever_skips_cycle_outside_hours_and_sleeps_interval` (`max_iterations=1`, fake sleep records `900`). Implement.
- [ ] **Step 3: Failing test** `test_run_forever_survives_cycle_exception`. With `max_iterations=2` and a first cycle raising `RuntimeError`, the second still runs. Implement.
- [ ] **Step 4: Failing test** `test_main_exits_2_on_config_error`. Use an env missing `TELEGRAM_BOT_TOKEN` via `monkeypatch`, and `pytest.raises(SystemExit)` with code 2. Implement.
- [ ] **Step 5: Manual check.** Point `CONFIG_DIR`/`STATE_DIR` at a `local/` dir holding real config and run `uv run youtube-summary --once` twice. Expected: the first run logs bootstrap; the second logs `sent=0` unless a new video appeared.
- [ ] **Step 6: Validate and commit** `feat: scheduling loop and cli entry point`.

---

### Task 9: Container and deployment tooling

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/ops/post-receive`, `scripts/notebooklm_session/sync_session.sh`, `scripts/notebooklm_session/.env.sample`, `.claude/skills/deploy/SKILL.md`, `.claude/skills/renew-notebooklm-session/SKILL.md`
- Test: CI image build (Task 1 job); `tests/test_repo_hygiene.py`

- [ ] **Step 1: `Dockerfile`.** Base `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`. Copy `pyproject.toml uv.lock .python-version`, `uv sync --frozen --no-dev --no-install-project`, copy `src/`, `uv sync --frozen --no-dev`. Set `ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1` and `CMD ["youtube-summary"]`. `.dockerignore` excludes `.git`, `.venv`, `tests`, `local`, `state`, `.env`, `config.yml`, `channels.yml`.
- [ ] **Step 2: `docker-compose.yml`:**

```yaml
services:
  youtube-summary:
    build: .
    restart: unless-stopped
    env_file: ${YST_HOME:?set YST_HOME}/config/.env
    environment:
      CONFIG_DIR: /config
      STATE_DIR: /state
      NOTEBOOKLM_STORAGE_STATE_PATH: /secrets/notebooklm/storage_state.json
    volumes:
      - ${YST_HOME}/config:/config:ro
      - ${YST_HOME}/state:/state
      - ${NOTEBOOKLM_SESSION_DIR:?set NOTEBOOKLM_SESSION_DIR}:/secrets/notebooklm
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
```

- [ ] **Step 3: `scripts/ops/post-receive`** (bash, `set -euo pipefail`). Variables are `YST_HOME="${YST_HOME:-$HOME/youtube-summary-telegram}"` and `APP_DIR="$YST_HOME/app"`. Read pushed refs from stdin and act only on `refs/heads/main`. Then `git --work-tree="$APP_DIR" --git-dir="$YST_HOME/repo.git" checkout -f main`, `cd "$APP_DIR"`, `docker compose --env-file "$YST_HOME/config/.env" up -d --build`, and finally `docker compose ... ps`. It never touches `config/` or `state/`.
- [ ] **Step 4: `scripts/notebooklm_session/sync_session.sh`.** Derive it from this behaviour:
  1. Source `.env` next to the script. Keys: `SERVER_SSH_TARGET`, `SERVER_SESSION_DIR`.
  2. Require `notebooklm`, `jq`, `rsync`.
  3. If `--force` is given, or `notebooklm auth check --test --json | jq -e '.status == "ok"'` fails, run `notebooklm login` and re-check.
  4. Rsync `~/.notebooklm/profiles/default/storage_state.json` to `$SERVER_SSH_TARGET:$SERVER_SESSION_DIR/storage_state.json` with `--chmod=u=rw,go-rwx`, creating the dir.

  `.env.sample` holds placeholders only.
- [ ] **Step 5: Skills.**
  - `deploy/SKILL.md` (frontmatter `name`, `description`). Steps:
    1. Run validation.
    2. `git push origin main`.
    3. `git push production main`.
    4. `ssh <target> 'cd $YST_HOME/app && docker compose --env-file $YST_HOME/config/.env logs --tail 50'`.
    5. Confirm a cycle log line.
  - `renew-notebooklm-session/SKILL.md`. Steps:
    1. Run `scripts/notebooklm_session/sync_session.sh --force`.
    2. Restart is not needed; the next cycle picks the session up.
    3. Check the logs for `sent=`/no auth error.

  Both use placeholders. They tell the agent to read real values from the owner's local, untracked `.env` files and never to write them into the repo.
- [ ] **Step 6: Failing test** `tests/test_repo_hygiene.py::test_no_secrets_or_private_values_committed`. It runs `git ls-files`, reads every tracked text file and asserts none matches the regexes for a Telegram bot token (`\d{8,10}:[A-Za-z0-9_-]{35}`), a negative chat id (`-100\d{6,}`) or an `@[\w.-]+\.(local|ts\.net)` host. The test must strip `GIT_*` variables from the subprocess env. Make it pass (it should already), then keep it.
- [ ] **Step 7: Local container check.** Set `YST_HOME=$PWD/local NOTEBOOKLM_SESSION_DIR=~/.notebooklm/profiles/default docker compose --env-file local/config/.env up --build`. Expected: the bootstrap log line, then the sleep.
- [ ] **Step 8: Validate and commit** `feat: docker image, compose, deploy hook and session sync`.

---

### Task 10: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README** for a public reader. Sections:
  - What it does: a one-paragraph description and a sample Telegram message in a code block.
  - How it works: the cycle in 6 bullets, including bootstrap and dedup.
  - Requirements: Docker, a Telegram bot (BotFather) added to the group, a Google account with NotebookLM.
  - Setup:
    1. Create a dedicated NotebookLM notebook; the id is the last URL path segment.
    2. Run `notebooklm login` on a desktop.
    3. Find a channel id (the `channel_id` in the page source or in `https://www.youtube.com/@handle` → "Share channel" → "Copy channel ID").
    4. Get a group chat id (`getUpdates`).
  - Configuration reference: every key of `.env`, `config.yml` and `channels.yml` with type, default and example.
  - Run locally: with uv (`--once`) and with Docker.
  - Deploy to your own server: bare repo + `post-receive`, directory layout `$YST_HOME/{repo.git,app,config,state}`, `git remote add production ...`, `git push production main`.
  - Renewing the NotebookLM session.
  - Operations: logs, resetting state (delete `seen.json` → bootstrap), adding a channel.
  - Troubleshooting: auth alert, video not ready, Telegram 400 (MarkdownV2), any 0.8.6 API differences found in Task 6.
  - Development: commands, TDD, CI.
  - License: Apache 2.0.

  Placeholders only.
- [ ] **Step 2: Check** every command in the README against the real files (paths, env names, flags).
- [ ] **Step 3: Validate and commit** `docs: README`.

---

### Task 11: Rollout (owner, on the real server)

Done by the owner or by an agent with the owner's SSH access. Real values come from the owner's private runbook and are never written to this repo.

- [ ] **Step 1:** Create the dedicated NotebookLM notebook in the same Google account that holds the existing session, and note its id.
- [ ] **Step 2:** On the server: `mkdir -p $YST_HOME/{app,config,state}`, `git init --bare $YST_HOME/repo.git`, install `scripts/ops/post-receive` as `$YST_HOME/repo.git/hooks/post-receive` (`chmod +x`).
- [ ] **Step 3:** Write `$YST_HOME/config/.env`, `config.yml` and `channels.yml` (`chmod 600 .env`). Point `NOTEBOOKLM_SESSION_DIR` at the directory holding the existing `storage_state.json`.
- [ ] **Step 4:** From the desktop: `git remote add production <ssh-target>:<YST_HOME>/repo.git && git push production main`.
- [ ] **Step 5:** Check the logs for the bootstrap line (`bootstrapped=True`) and confirm `seen.json` exists.
- [ ] **Step 6:** Immediately decommission the previous system's YouTube schedules (private repo; see the spec section "Decommissioning the previous system").
- [ ] **Step 7:** Wait for the first new video from a configured channel. Confirm it arrives in the Telegram group exactly once, formatted correctly.

---

## Merge requests

- **Count: 0 PRs. Commits go directly to `main`,** one per task (Tasks 1–10). The repo is new and single-owner; the owner works on `main` without feature branches. CI runs on every push, so a red CI after a task push is fixed before the next task.
- **If the owner prefers review:** 1 PR, `feat/initial-service` → `main`, containing Tasks 1–10, merged once CI is green. Splitting further adds no review value, because every task depends on the previous one.
- **Task 11** is operational and produces no commit in this repo. The decommissioning lands as one separate commit in the owner's private repo, after Step 5.
- **Dependencies:** each task depends on the previous ones. Task 11 needs Tasks 1–10 on `main`.

## Self-review notes

- Every spec section maps to tasks: config → 2; feed + filter → 3; state → 4; message format + auth text → 5; adapters → 6; cycle steps 1–6 → 7; active hours + CLI → 8; deploy + session → 9; README → 10; CLAUDE.md + CI → 1; skills → 9; rollout + decommission → 11.
- The Review Focus items are pinned in Tasks 4, 5 and 7.
- Names used across tasks: `Video`, `Channel`, `AppConfig`, `ActiveHours`, `Settings`, `Summary`, `SeenState`, `CycleDeps`, `CycleResult`, `run_cycle`, `NotebookLMSummarizer`, `TelegramClient`.
