from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from notebooklm.exceptions import (
    AuthError,
    HeadlessReauthError,
    NotebookLMError,
    SourceError,
    SourceTimeoutError,
)

from youtube_summary.summarizer import (
    NotebookAuthError,
    NotebookLMSummarizer,
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
    def __init__(self, sources: list[str], fail: Exception | None = None) -> None:
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

    async def delete(self, notebook_id: str, source_id: str) -> None:
        self.calls.append(("delete", notebook_id, source_id))

    async def add_url(self, notebook_id: str, url: str, *, wait: bool) -> None:
        assert wait is True
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
    kinds = [call[0] for call in client.calls]
    assert kinds == ["list", "delete", "delete", "add_url", "ask"]
    assert [c[2] for c in client.calls if c[0] == "delete"] == ["a", "b"]
    assert client.calls[-1] == ("ask", "nb", "prompt")


async def test_ping_lists_notebooks() -> None:
    client = _FakeClient([])
    await _summarizer(client).ping()
    assert client.calls == [("list",)]


_ERRORS: list[tuple[Exception, type[Exception]]] = [
    (SourceError("x"), VideoNotReadyError),
    (SourceTimeoutError("s", 1.0), VideoNotReadyError),
    (AuthError("x"), NotebookAuthError),
    (HeadlessReauthError("x"), NotebookAuthError),
    (NotebookLMError("x"), SummarizerError),
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
