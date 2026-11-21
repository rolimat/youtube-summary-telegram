import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
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
    def __init__(
        self,
        deps: CycleDeps,
        errors: list[Exception] | None = None,
        duration: int = 0,
        sleeps: int = 1,
    ) -> None:
        self.deps = deps
        self.errors = errors or []
        self.max_sleeps = sleeps
        self.duration = timedelta(seconds=duration)
        self.now = OUTSIDE
        self.within_hours: list[bool] = []
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        return self.now

    async def cycle(self, deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
        self.within_hours.append(within_hours)
        self.now += self.duration
        if self.errors:
            raise self.errors.pop(0)
        return CycleResult()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) >= self.max_sleeps:
            self.deps.stop.set()


async def drive(recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "run_cycle", recorder.cycle)
    await run_forever(recorder.deps, clock=recorder.clock, sleep=recorder.sleep)


async def test_run_forever_passes_within_hours_and_sleeps_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))
    await drive(recorder, monkeypatch)
    assert recorder.within_hours == [False]
    assert recorder.sleeps == [900]


async def test_run_forever_survives_cycle_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder(make_deps(tmp_path), errors=[RuntimeError("boom")], sleeps=2)
    await drive(recorder, monkeypatch)
    assert recorder.within_hours == [False, False]
    assert "boom" in caplog.text


async def test_run_forever_subtracts_cycle_duration_from_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path), duration=300)
    await drive(recorder, monkeypatch)
    assert recorder.sleeps == [600]


async def test_run_forever_does_not_sleep_negative_after_long_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path), duration=1200)
    await drive(recorder, monkeypatch)
    assert recorder.sleeps == [0]


async def test_run_forever_returns_when_stop_is_set_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))
    recorder.deps.stop.set()
    await drive(recorder, monkeypatch)
    assert recorder.within_hours == []


async def test_run_forever_skips_sleep_when_stop_set_during_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder(make_deps(tmp_path))

    async def stopping_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
        deps.stop.set()
        return CycleResult()

    monkeypatch.setattr(main_module, "run_cycle", stopping_cycle)
    await run_forever(recorder.deps, clock=recorder.clock, sleep=recorder.sleep)
    assert recorder.sleeps == []


async def test_on_signal_first_sets_stop_second_cancels() -> None:
    stop = asyncio.Event()
    task = asyncio.ensure_future(asyncio.sleep(3600))
    main_module._on_signal(stop, task)
    await asyncio.sleep(0)
    assert stop.is_set()
    assert not task.cancelled()
    main_module._on_signal(stop, task)
    with pytest.raises(asyncio.CancelledError):
        await task


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


def test_main_once_exits_1_and_logs_on_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def failing_cycle(deps: CycleDeps, now: datetime, *, within_hours: bool) -> CycleResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "build_deps", lambda s, h, stop: make_deps(tmp_path))
    monkeypatch.setattr(main_module, "run_cycle", failing_cycle)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("NOTEBOOKLM_STORAGE_STATE_PATH", str(tmp_path / "s.json"))
    with pytest.raises(SystemExit) as exit_info:
        main_module.main(["--once"])
    assert exit_info.value.code == 1
    assert "Iteration failed" in caplog.text
