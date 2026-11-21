import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from youtube_summary.config import AppConfig, Channel
from youtube_summary.feed import FeedError, Video, matches_title_filter
from youtube_summary.message import (
    EmptyAnswerError,
    format_auth_alert,
    format_message,
    parse_answer,
)
from youtube_summary.state import PendingMessage, SeenState
from youtube_summary.summarizer import (
    NotebookAuthError,
    NotebookQuotaError,
    SummarizerError,
    VideoNotReadyError,
)

logger = logging.getLogger(__name__)
MAX_SEND_ATTEMPTS = 3
MAX_SUMMARIZE_FAILURES = 3
PRUNE_MARGIN = timedelta(hours=24)


class Summarizer(Protocol):
    async def summarize(self, video_url: str, prompt: str) -> str: ...

    async def ping(self) -> None: ...


class Sender(Protocol):
    async def send(self, text: str) -> bool: ...


FetchVideos = Callable[[Channel], Awaitable[list[Video]]]


@dataclass(frozen=True)
class CycleDeps:
    config: AppConfig
    channels: tuple[Channel, ...]
    state_path: Path
    fetch: FetchVideos
    summarizer: Summarizer
    sender: Sender
    stop: asyncio.Event


@dataclass
class CycleResult:
    bootstrapped: bool = False
    sent: int = 0
    retried: int = 0
    deferred: int = 0
    failed: int = 0
    given_up: int = 0
    feed_errors: int = 0
    auth_failed: bool = False
    quota_exhausted: bool = False


async def run_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
    result = CycleResult()
    state = SeenState.load(deps.state_path)
    if state is None:
        await _bootstrap(deps, now, result)
        return result
    pinged = await _keepalive(deps, result)
    if within_hours and not result.auth_failed:
        await _deliver(deps, state, now, result)
    if result.auth_failed:
        if within_hours:
            await _handle_auth_failure(deps, state)
    elif pinged:
        state.auth_alert_sent = False
    state.prune(now, timedelta(hours=deps.config.max_video_age_hours) + PRUNE_MARGIN)
    state.save()
    logger.info("Iteration done: %s", result)
    return result


async def _deliver(deps: CycleDeps, state: SeenState, now: datetime, result: CycleResult) -> None:
    if not await _retry_pending(deps, state, now, result) or deps.stop.is_set():
        return
    for video in await _collect_candidates(deps, state, now, result):
        if deps.stop.is_set() or not await _process_video(deps, state, video, now, result):
            return


async def _keepalive(deps: CycleDeps, result: CycleResult) -> bool:
    try:
        await deps.summarizer.ping()
    except NotebookAuthError:
        _auth_failed(result)
        return False
    except SummarizerError as error:
        logger.warning("NotebookLM keepalive failed: %s", error)
        return False
    return True


def _auth_failed(result: CycleResult) -> None:
    result.auth_failed = True
    logger.error("NotebookLM session expired; renew storage_state.json")


async def _handle_auth_failure(deps: CycleDeps, state: SeenState) -> None:
    if not state.auth_alert_sent and await deps.sender.send(format_auth_alert(deps.config.texts)):
        state.auth_alert_sent = True
        state.save()


def _label(video: Video) -> str:
    return f"{video.channel_name} — {video.title}"


def _is_candidate(state: SeenState, channel: Channel, video: Video, oldest: datetime) -> bool:
    return (
        not state.is_seen(video.video_id)
        and video.video_id not in state.pending
        and video.published_at > oldest
        and matches_title_filter(video.title, channel.title_filter)
    )


async def _collect_candidates(
    deps: CycleDeps, state: SeenState, now: datetime, result: CycleResult
) -> list[Video]:
    oldest = now - timedelta(hours=deps.config.max_video_age_hours)
    videos: dict[str, Video] = {}
    for channel, fetched in zip(deps.channels, await _fetch_all(deps, result), strict=True):
        for video in fetched or []:
            if _is_candidate(state, channel, video, oldest):
                videos.setdefault(video.video_id, video)
    return sorted(videos.values(), key=lambda video: video.published_at)


async def _summarize(deps: CycleDeps, video: Video) -> list[str]:
    answer = await deps.summarizer.summarize(video.url, deps.config.prompt)
    return format_message(video, parse_answer(answer), deps.config.texts)


async def _process_video(
    deps: CycleDeps, state: SeenState, video: Video, now: datetime, result: CycleResult
) -> bool:
    try:
        chunks = await _summarize(deps, video)
    except VideoNotReadyError:
        result.deferred += 1
        logger.info("Deferred, not processable yet: %s", _label(video))
        return True
    except NotebookQuotaError as error:
        result.quota_exhausted = True
        logger.warning("NotebookLM quota exhausted (%s); paused, %s waits", error, _label(video))
        return False
    except NotebookAuthError:
        _auth_failed(result)
        return False
    except Exception as error:
        _summarize_failed(state, video, now, result, error)
        return True
    return await _store_and_send(deps, state, video, chunks, now, result)


async def _store_and_send(
    deps: CycleDeps,
    state: SeenState,
    video: Video,
    chunks: list[str],
    now: datetime,
    result: CycleResult,
) -> bool:
    state.clear_summarize_failure(video.video_id)
    state.pending[video.video_id] = PendingMessage(_label(video), chunks)
    state.save()
    if not await _send_pending(deps, state, video.video_id, now, result):
        return False
    result.sent += 1
    return True


def _summarize_failed(
    state: SeenState, video: Video, now: datetime, result: CycleResult, error: Exception
) -> None:
    if not isinstance(error, (SummarizerError, EmptyAnswerError)):
        logger.exception("Unexpected error summarising %s", _label(video))
    count = state.record_summarize_failure(video.video_id, now)
    if count >= MAX_SUMMARIZE_FAILURES:
        logger.error("Given up summarising %s (%s): %s", _label(video), video.video_id, error)
        _close(state, video.video_id, now)
        result.given_up += 1
    else:
        result.failed += 1
        limit = MAX_SUMMARIZE_FAILURES
        logger.info("Summarise failed %d/%d: %s: %s", count, limit, _label(video), error)
    state.save()


def _close(state: SeenState, video_id: str, now: datetime) -> None:
    state.pending.pop(video_id, None)
    state.mark_seen(video_id, now)
    state.clear_summarize_failure(video_id)


async def _retry_pending(
    deps: CycleDeps, state: SeenState, now: datetime, result: CycleResult
) -> bool:
    for video_id in list(state.pending):
        if not await _send_pending(deps, state, video_id, now, result):
            return False
        result.retried += 1
    return True


async def _send_pending(
    deps: CycleDeps, state: SeenState, video_id: str, now: datetime, result: CycleResult
) -> bool:
    message = state.pending[video_id]
    while message.chunks:
        if deps.stop.is_set():
            return False
        if not await deps.sender.send(message.chunks[0]):
            _send_rejected(state, video_id, now, result)
            return False
        message.chunks.pop(0)
        state.save()
    _close(state, video_id, now)
    state.save()
    logger.info("Sent: %s", message.label)
    return True


def _send_rejected(state: SeenState, video_id: str, now: datetime, result: CycleResult) -> None:
    message = state.pending[video_id]
    message.send_attempts += 1
    if message.send_attempts >= MAX_SEND_ATTEMPTS:
        logger.error("Given up sending %s (%s)", message.label, video_id)
        _close(state, video_id, now)
        result.given_up += 1
    else:
        attempts = message.send_attempts
        logger.info("Send failed %d/%d: %s", attempts, MAX_SEND_ATTEMPTS, message.label)
    state.save()


async def _fetch(deps: CycleDeps, channel: Channel, result: CycleResult) -> list[Video] | None:
    try:
        return await deps.fetch(channel)
    except FeedError as error:
        logger.warning("Feed for %s failed: %s", channel.name, error)
        result.feed_errors += 1
        return None


async def _fetch_all(deps: CycleDeps, result: CycleResult) -> list[list[Video] | None]:
    return await asyncio.gather(*(_fetch(deps, channel, result) for channel in deps.channels))


async def _bootstrap(deps: CycleDeps, now: datetime, result: CycleResult) -> None:
    if deps.stop.is_set():
        return
    fetched = await _fetch_all(deps, result)
    failed = [c.name for c, videos in zip(deps.channels, fetched, strict=True) if videos is None]
    if failed:
        hint = "fix or remove it from channels.yml"
        logger.error("Bootstrap aborted, feed failed: %s; %s", ", ".join(failed), hint)
        return
    state = SeenState.empty(deps.state_path)
    for videos in fetched:
        for video in videos or []:
            state.mark_seen(video.video_id, now)
    state.save()
    result.bootstrapped = True
    logger.info("Bootstrapped state: existing feed entries marked seen, nothing sent")
