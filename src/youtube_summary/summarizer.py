import asyncio
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from pathlib import Path
from typing import Any

from notebooklm import NotebookLMClient
from notebooklm.exceptions import AuthError, HeadlessReauthError, NotebookLMError, SourceError

_SUMMARIZE_TIMEOUT_SECONDS = 900
_PING_TIMEOUT_SECONDS = 60


class VideoNotReadyError(Exception): ...


class NotebookAuthError(Exception): ...


class SummarizerError(Exception): ...


@contextmanager
def _mapped_errors() -> Iterator[None]:
    try:
        yield
    except SourceError as error:
        raise VideoNotReadyError(str(error)) from error
    except (AuthError, HeadlessReauthError, FileNotFoundError) as error:
        raise NotebookAuthError(str(error)) from error
    except (NotebookLMError, TimeoutError) as error:
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
                for source in await c.sources.list(self._notebook_id):
                    await c.sources.delete(self._notebook_id, source.id)
                await c.sources.add_url(self._notebook_id, video_url, wait=True)
                result = await c.chat.ask(self._notebook_id, prompt)
                return str(result.answer)

    async def ping(self) -> None:
        with _mapped_errors():
            async with asyncio.timeout(_PING_TIMEOUT_SECONDS), self._factory(self._path) as c:
                await c.notebooks.list()
