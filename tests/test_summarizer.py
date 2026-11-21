import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from notebooklm._auth.cookie_policy import RequiredCookieValidationError
from notebooklm._auth.cookie_semantics import CookieRowError
from notebooklm._auth.cookie_types import StorageStateValidationError
from notebooklm.exceptions import (
    AuthError,
    HeadlessReauthError,
    NotebookLMError,
    RateLimitError,
    SourceError,
    SourceTimeoutError,
)

from youtube_summary.summarizer import (
    NotebookAuthError,
    NotebookLMSummarizer,
    NotebookQuotaError,
    SummarizerError,
    VideoNotReadyError,
)


@dataclass
class _Source:
    id: str


@dataclass
class _Answer:
    answer: str


class _FakeClient:
    def __init__(
        self, sources: list[str], fail: Exception | None = None, conversation: str | None = None
    ) -> None:
        self._conversation = conversation
        self.calls: list[tuple[str, ...]] = []
        self._sources = sources
        self._fail = fail
        self.sources = self
        self.notebooks = self
        self.chat = self

    async def list(self, *args: str) -> list[_Source]:
        self.calls.append(("list", *args))
        if self._fail:
            raise self._fail
        return [_Source(i) for i in self._sources]

    async def delete_many(self, notebook_id: str, source_ids: Sequence[str]) -> None:
        self.calls.append(("delete_many", notebook_id, *source_ids))

    async def get_conversation_id(self, notebook_id: str) -> str | None:
        self.calls.append(("get_conversation_id", notebook_id))
        return self._conversation

    async def delete_conversation(self, notebook_id: str, conversation_id: str) -> None:
        self.calls.append(("delete_conversation", notebook_id, conversation_id))

    async def add_url(self, notebook_id: str, url: str, *, wait: bool, wait_timeout: float) -> None:
        assert (wait, wait_timeout) == (True, 600)
        self.calls.append(("add_url", notebook_id, url))
        if self._fail:
            raise self._fail

    async def ask(self, notebook_id: str, prompt: str) -> _Answer:
        self.calls.append(("ask", notebook_id, prompt))
        return _Answer("the answer")


def _summarizer(
    client: _FakeClient, factory: Callable[[str], AbstractAsyncContextManager[Any]] | None = None
) -> NotebookLMSummarizer:
    @asynccontextmanager
    async def default(path: str) -> Any:
        assert path == "/s.json"
        yield client

    return NotebookLMSummarizer(Path("/s.json"), "nb", factory or default)


async def test_summarize_replaces_sources_and_returns_answer() -> None:
    client = _FakeClient(["a", "b"])
    answer = await _summarizer(client).summarize("https://y/v", "prompt")
    assert answer == "the answer"
    assert client.calls == [
        ("list", "nb"),
        ("delete_many", "nb", "a", "b"),
        ("add_url", "nb", "https://y/v"),
        ("get_conversation_id", "nb"),
        ("ask", "nb", "prompt"),
    ]


async def test_summarize_deletes_existing_conversation_before_ask() -> None:
    client = _FakeClient([], conversation="c1")
    await _summarizer(client).summarize("u", "p")
    kinds = [call[0] for call in client.calls]
    assert "delete_many" not in kinds
    assert client.calls[-2:] == [("delete_conversation", "nb", "c1"), ("ask", "nb", "p")]


async def test_ping_lists_notebooks() -> None:
    client = _FakeClient([])
    await _summarizer(client).ping()
    assert client.calls == [("list",)]


_ERRORS: list[tuple[Exception, type[Exception]]] = [
    (SourceError("x"), VideoNotReadyError),
    (SourceTimeoutError("s", 1.0), VideoNotReadyError),
    (AuthError("x"), NotebookAuthError),
    (HeadlessReauthError("x"), NotebookAuthError),
    (StorageStateValidationError("x"), NotebookAuthError),
    (RequiredCookieValidationError("x"), NotebookAuthError),
    (CookieRowError("f", "r"), NotebookAuthError),
    (json.JSONDecodeError("x", "", 0), NotebookAuthError),
    (RateLimitError("x"), NotebookQuotaError),
    (NotebookLMError("x"), SummarizerError),
    (httpx.ConnectError("x"), SummarizerError),
    (TimeoutError(), SummarizerError),
]


@pytest.mark.parametrize(("error", "expected"), _ERRORS)
async def test_summarizer_maps_library_errors(error: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected) as info:
        await _summarizer(_FakeClient([], error)).summarize("u", "p")
    assert type(info.value) is expected


@pytest.mark.parametrize(("error", "expected"), _ERRORS[2:])
async def test_ping_maps_library_errors(error: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        await _summarizer(_FakeClient([], error)).ping()


async def test_missing_storage_file_maps_to_auth_error(tmp_path: Path) -> None:
    @asynccontextmanager
    async def missing(path: str) -> Any:
        raise FileNotFoundError(path)
        yield

    with pytest.raises(NotebookAuthError):
        await _summarizer(_FakeClient([]), missing).ping()
