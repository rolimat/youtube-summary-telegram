import asyncio
import json
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx
from notebooklm import NotebookLMClient
from notebooklm._auth.cookie_policy import RequiredCookieValidationError
from notebooklm._auth.cookie_semantics import CookieRowError
from notebooklm._auth.cookie_types import StorageStateValidationError
from notebooklm.exceptions import (
    AuthError,
    HeadlessReauthError,
    NotebookLMError,
    RateLimitError,
    SourceError,
)

_SUMMARIZE_TIMEOUT_SECONDS = 900
_PING_TIMEOUT_SECONDS = 60
_SOURCE_WAIT_SECONDS = 600
_DEFAULT_QUOTA_PAUSE_SECONDS = 3600
_AUTH_ERRORS = (AuthError, HeadlessReauthError)
_ENTER_AUTH_ERRORS = (
    FileNotFoundError,
    StorageStateValidationError,
    RequiredCookieValidationError,
    CookieRowError,
    json.JSONDecodeError,
)
_LIBRARY_ERRORS = (NotebookLMError, TimeoutError, httpx.HTTPError, *_ENTER_AUTH_ERRORS)


class VideoNotReadyError(Exception): ...


class NotebookAuthError(Exception): ...


class SummarizerError(Exception): ...


class NotebookQuotaError(SummarizerError): ...


@contextmanager
def _mapped_errors() -> Iterator[None]:
    try:
        yield
    except SourceError as error:
        raise VideoNotReadyError(str(error)) from error
    except _AUTH_ERRORS as error:
        raise NotebookAuthError(str(error)) from error
    except _LIBRARY_ERRORS as error:
        raise SummarizerError(f"{type(error).__name__}: {error}") from error


class NotebookLMSummarizer:
    def __init__(
        self,
        storage_state_path: Path,
        notebook_id: str,
        client_factory: Callable[[str], AbstractAsyncContextManager[Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._paused_until = 0.0
        self._path = str(storage_state_path)
        self._notebook_id = notebook_id
        self._factory = client_factory or NotebookLMClient.from_storage

    async def summarize(self, video_url: str, prompt: str) -> str:
        if self._clock() < self._paused_until:
            raise NotebookQuotaError("NotebookLM chat quota pause in effect")
        with _mapped_errors():
            async with asyncio.timeout(_SUMMARIZE_TIMEOUT_SECONDS), AsyncExitStack() as stack:
                client = await self._enter(stack)
                await self._reset(client, video_url)
                return await self._ask(client, prompt)

    async def ping(self) -> None:
        with _mapped_errors():
            async with asyncio.timeout(_PING_TIMEOUT_SECONDS), AsyncExitStack() as stack:
                await (await self._enter(stack)).notebooks.list()

    async def _enter(self, stack: AsyncExitStack) -> Any:
        try:
            return await stack.enter_async_context(self._factory(self._path))
        except _ENTER_AUTH_ERRORS as error:
            raise NotebookAuthError(str(error)) from error

    async def _ask(self, client: Any, prompt: str) -> str:
        try:
            result = await client.chat.ask(self._notebook_id, prompt)
        except RateLimitError as error:
            wait = error.retry_after
            self._paused_until = self._clock() + (
                wait if wait and wait > 0 else _DEFAULT_QUOTA_PAUSE_SECONDS
            )
            raise NotebookQuotaError(f"{type(error).__name__}: {error}") from error
        return str(result.answer)

    async def _reset(self, client: Any, video_url: str) -> None:
        sources = await client.sources.list(self._notebook_id)
        if sources:
            await client.sources.delete_many(self._notebook_id, [s.id for s in sources])
        await client.sources.add_url(
            self._notebook_id, video_url, wait=True, wait_timeout=_SOURCE_WAIT_SECONDS
        )
        await reset_conversation(client, self._notebook_id)


async def reset_conversation(client: Any, notebook_id: str) -> None:
    conversation_id = await client.chat.get_conversation_id(notebook_id)
    if conversation_id is not None:
        await client.chat.delete_conversation(notebook_id, conversation_id)
