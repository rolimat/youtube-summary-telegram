import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial

import httpx

from youtube_summary.config import (
    ActiveHours,
    ConfigError,
    Settings,
    load_app_config,
    load_channels,
    load_settings,
)
from youtube_summary.cycle import CycleDeps, run_cycle
from youtube_summary.feed import fetch_channel_videos
from youtube_summary.summarizer import NotebookLMSummarizer
from youtube_summary.telegram import TelegramClient

logger = logging.getLogger(__name__)
HTTP_TIMEOUT_SECONDS = 30


def is_within_active_hours(now: datetime, hours: ActiveHours) -> bool:
    return hours.start <= now.astimezone(hours.timezone).hour <= hours.end


async def interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def _safe_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> bool:
    try:
        await run_cycle(deps, now, within_hours=within_hours)
    except Exception:
        logger.exception("Iteration failed")
        return False
    return True


async def run_forever(
    deps: CycleDeps,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    interval = deps.config.poll_interval_minutes * 60
    while not deps.stop.is_set():
        started = clock()
        await _safe_cycle(
            deps, started, within_hours=is_within_active_hours(started, deps.config.active_hours)
        )
        if deps.stop.is_set():
            return
        await sleep(max(0.0, interval - (clock() - started).total_seconds()))


def build_deps(settings: Settings, http: httpx.AsyncClient, stop: asyncio.Event) -> CycleDeps:
    config = load_app_config(settings.config_dir / "config.yml")
    return CycleDeps(
        config=config,
        channels=load_channels(settings.config_dir / "channels.yml"),
        state_path=settings.state_dir / "seen.json",
        fetch=partial(fetch_channel_videos, http),
        summarizer=NotebookLMSummarizer(settings.storage_state_path, config.notebook_id),
        sender=TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, http),
        stop=stop,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _on_signal(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    if stop.is_set():
        task.cancel()
    stop.set()


async def _run(settings: Settings, once: bool) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, _on_signal, stop, task)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as http:
        deps = build_deps(settings, http, stop)
        if not once:
            await run_forever(deps, clock=_now, sleep=partial(interruptible_sleep, stop))
        elif not await _safe_cycle(deps, _now(), within_hours=True):
            raise SystemExit(1)


def _setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="youtube-summary")
    parser.add_argument("--once", action="store_true", help="run one iteration and exit")
    args = parser.parse_args(argv)
    _setup_logging()
    try:
        asyncio.run(_run(load_settings(os.environ), args.once))
    except asyncio.CancelledError:
        logger.warning("Interrupted by second signal")
    except ConfigError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
