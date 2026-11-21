import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
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
_AUTH_ERRORS = (
    AuthError,
    HeadlessReauthError,
    FileNotFoundError,
    StorageStateValidationError,
    RequiredCookieValidationError,
    CookieRowError,
    json.JSONDecodeError,
)


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
    except RateLimitError as error:
        raise NotebookQuotaError(str(error)) from error
    except _AUTH_ERRORS as error:
        raise NotebookAuthError(str(error)) from error
    except (NotebookLMError, TimeoutError, httpx.HTTPError) as error:
        raise SummarizerError(f"{type(error).__name__}: {error}") from error


class NotebookLMSummarizer:
    def __init__(
        self,
        storage_state_path: Path,
        notebook_id: str,
        client_factory: Callable[[str], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self._path = str(storage_state_path)
        self._notebook_id = notebook_id
        self._factory = client_factory or NotebookLMClient.from_storage

    async def summarize(self, video_url: str, prompt: str) -> str:
        with _mapped_errors():
            async with asyncio.timeout(_SUMMARIZE_TIMEOUT_SECONDS), self._factory(self._path) as c:
                await self._reset(c, video_url)
                result = await c.chat.ask(self._notebook_id, prompt)
                return str(result.answer)

    async def ping(self) -> None:
        with _mapped_errors():
            async with asyncio.timeout(_PING_TIMEOUT_SECONDS), self._factory(self._path) as c:
                await c.notebooks.list()

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
