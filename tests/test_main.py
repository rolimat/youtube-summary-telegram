import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FakeSender, FakeSummarizer, fake_fetch, make_config

from youtube_summary import main as main_module
from youtube_summary.cycle import CycleDeps, CycleResult
from youtube_summary.main import interruptible_sleep, is_within_active_hours, run_forever

MADRID = make_config().active_hours
OUTSIDE = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 11, 20, 5, 59, tzinfo=UTC), False),
        (datetime(2026, 11, 20, 6, 0, tzinfo=UTC), True),
        (datetime(2026, 11, 20, 22, 59, tzinfo=UTC), True),
        (datetime(2026, 11, 20, 23, 30, tzinfo=UTC), False),
    ],
)
def test_is_within_active_hours(utc: datetime, expected: bool) -> None:
    assert is_within_active_hours(utc, MADRID) is expected


def make_deps(tmp_path: Path) -> CycleDeps:
    return CycleDeps(
        config=make_config(poll_interval_minutes=15),
        channels=(),
        state_path=tmp_path / "seen.json",
        fetch=fake_fetch({}),
        summarizer=FakeSummarizer(),
        sender=FakeSender(),
        stop=asyncio.Event(),
    )


class Recorder:
    def __init__(self, deps: CycleDeps, errors: list[Exception] | None = None) -> None:
        self.deps = deps
        self.errors = errors or []
        self.within_hours: list[bool] = []
        self.sleeps: list[float] = []

    async def cycle(self, deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
        self.within_hours.append(within_hours)
        if self.errors:
            raise self.errors.pop(0)
        return CycleResult()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def drive(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, max_iterations: int | None
) -> None:
    monkeypatch.setattr(main_module, "run_cycle", recorder.cycle)
    await run_forever(
        recorder.deps,
        clock=lambda: OUTSIDE,
        sleep=recorder.sleep,
        max_iterations=max_iterations,
    )


async def test_run_forever_passes_within_hours_and_sleeps_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))
    await drive(recorder, monkeypatch, 1)
    assert recorder.within_hours == [False]
    assert recorder.sleeps == [900]


async def test_run_forever_survives_cycle_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder(make_deps(tmp_path), errors=[RuntimeError("boom")])
    await drive(recorder, monkeypatch, 2)
    assert recorder.within_hours == [False, False]
    assert "boom" in caplog.text


async def test_run_forever_returns_when_stop_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))
    recorder.deps.stop.set()
    await drive(recorder, monkeypatch, None)
    assert recorder.sleeps == []


async def test_run_forever_returns_after_sleep_when_stop_set_during_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))

    async def stopping_sleep(seconds: float) -> None:
        recorder.deps.stop.set()

    monkeypatch.setattr(main_module, "run_cycle", recorder.cycle)
    await run_forever(recorder.deps, clock=lambda: OUTSIDE, sleep=stopping_sleep)
    assert recorder.within_hours == [False]


async def test_interruptible_sleep_returns_early_on_stop() -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.01, stop.set)
    started = time.monotonic()
    await interruptible_sleep(stop, 3600)
    assert time.monotonic() - started < 1


async def test_interruptible_sleep_waits_full_duration_without_stop() -> None:
    await interruptible_sleep(asyncio.Event(), 0.01)


def test_main_exits_2_on_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main_module.main([])
    assert exit_info.value.code == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_main_silences_httpx_info_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)
    with pytest.raises(SystemExit):
        main_module.main([])
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_main_once_runs_a_single_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
        calls.append(within_hours)
        return CycleResult()

    monkeypatch.setattr(main_module, "build_deps", lambda s, h, stop: make_deps(tmp_path))
    monkeypatch.setattr(main_module, "run_cycle", fake_cycle)
    for key, value in {
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_CHAT_ID": "c",
        "NOTEBOOKLM_STORAGE_STATE_PATH": str(tmp_path / "s.json"),
    }.items():
        monkeypatch.setenv(key, value)
    main_module.main(["--once"])
    assert calls == [True]
