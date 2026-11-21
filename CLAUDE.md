# youtube-summary-telegram

## Core principles
- Baby steps; TDD, one failing test at a time.
- Full typing (`mypy --strict`).
- Small functions (max 20 lines), clear names, no comments.
- Simplest solution that works; flag repeated code.

## Commands
- `uv sync`
- `uv run pytest -q`
- `uv run mypy src tests`
- `uv run pre-commit run --all-files`
- `uv run youtube-summary --once`

## Testing
- Mock every external API (YouTube, NotebookLM, Telegram).
- Use `httpx.MockTransport` for HTTP.
- No network in tests.

## Security
Never commit `.env`, `config.yml`, `channels.yml`, `storage_state.json`, `seen.json`,
`scripts/server.env`, tokens, chat ids, notebook ids, hostnames, usernames or server paths.

## Independence
This service never depends on another app's files, sessions, bots or containers.

## NotebookLM budget
Usage is limited. Never add code paths that call NotebookLM again for a video already summarised.

## Dependencies
Updates arrive as Dependabot PRs. `notebooklm-py` and major updates need
`scripts/smoke_notebooklm.py` before merging.

## Validation before every commit
Pre-commit plus the full pytest run, zero errors.

## Commits
Conventional Commits.

## Docs map
- `README.md`
- `docs/superpowers/specs/` design spec
- `docs/superpowers/plans/` implementation plan
