import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import (
    NOW,
    FakeSender,
    FakeSummarizer,
    fake_fetch,
    make_channel,
    make_config,
    make_video,
)

from youtube_summary.config import Channel
from youtube_summary.cycle import CycleDeps, CycleResult, FetchVideos, run_cycle
from youtube_summary.feed import FeedError, Video
from youtube_summary.message import format_auth_alert
from youtube_summary.state import PendingMessage, SeenState
from youtube_summary.summarizer import (
    NotebookAuthError,
    SummarizerError,
    VideoNotReadyError,
)


def make_deps(
    tmp_path: Path,
    fetch: FetchVideos,
    summarizer: FakeSummarizer | None = None,
    sender: FakeSender | None = None,
    channels: tuple[Channel, ...] = (make_channel(),),
) -> CycleDeps:
    return CycleDeps(
        config=make_config(),
        channels=channels,
        state_path=tmp_path / "seen.json",
        fetch=fetch,
        summarizer=summarizer or FakeSummarizer(),
        sender=sender or FakeSender(),
        stop=asyncio.Event(),
    )


def seed_state(tmp_path: Path, seen: tuple[str, ...] = ()) -> SeenState:
    state = SeenState.empty(tmp_path / "seen.json")
    for video_id in seen:
        state.mark_seen(video_id, NOW)
    state.save()
    return state


def load(tmp_path: Path) -> SeenState:
    state = SeenState.load(tmp_path / "seen.json")
    assert state is not None
    return state


async def run(deps: CycleDeps, *, within_hours: bool = True) -> CycleResult:
    return await run_cycle(deps, NOW, within_hours=within_hours)


def feed(*videos: Video) -> FetchVideos:
    return fake_fetch({"Chan": list(videos)})


async def test_first_run_bootstraps_without_sending(tmp_path: Path) -> None:
    sender = FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a"), make_video("b")), sender=sender)

    result = await run(deps)

    assert result.bootstrapped and result.sent == 0 and sender.texts == []
    assert deps.state_path.exists()
    state = load(tmp_path)
    assert state.is_seen("a") and state.is_seen("b")


async def test_new_video_is_summarised_sent_and_marked_seen(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer, sender = FakeSummarizer(), FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a")), summarizer, sender)

    result = await run(deps)

    assert result.sent == 1 and not result.bootstrapped
    assert summarizer.summarize_calls == ["https://www.youtube.com/watch?v=a"]
    assert len(sender.texts) == 1 and "Resumen corto." in sender.texts[0]
    state = load(tmp_path)
    assert state.is_seen("a") and state.pending == {}


async def test_keepalive_runs_every_iteration_even_outside_hours(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer, sender = FakeSummarizer(), FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a")), summarizer, sender)

    await run(deps, within_hours=False)

    assert summarizer.ping_calls == 1
    assert summarizer.summarize_calls == [] and sender.texts == []
    assert not load(tmp_path).is_seen("a")


async def test_keepalive_auth_failure_alerts_once(tmp_path: Path) -> None:
    seed_state(tmp_path)
    fetched: list[Channel] = []

    async def fetch(channel: Channel) -> list[Video]:
        fetched.append(channel)
        return [make_video("a")]

    summarizer = FakeSummarizer(ping_error=NotebookAuthError("expired"))
    sender = FakeSender()
    deps = make_deps(tmp_path, fetch, summarizer, sender)

    result = await run(deps)
    again = await run(deps)

    assert result.auth_failed and again.auth_failed
    assert sender.texts == [format_auth_alert()]
    assert fetched == [] and summarizer.summarize_calls == []
    assert load(tmp_path).auth_alert_sent
    summarizer.ping_error = None
    await run(deps, within_hours=False)
    assert not load(tmp_path).auth_alert_sent


async def test_keepalive_other_error_does_not_stop_iteration(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer = FakeSummarizer(ping_error=SummarizerError("boom"))
    deps = make_deps(tmp_path, feed(make_video("a")), summarizer)

    result = await run(deps)

    assert result.sent == 1 and not result.auth_failed


async def test_skips_seen_pending_old_and_filtered_videos(tmp_path: Path) -> None:
    state = seed_state(tmp_path, seen=("seen",))
    state.pending["pend"] = PendingMessage("Chan — Pend", ["texto"])
    state.save()
    videos = [
        make_video("seen"),
        make_video("pend"),
        make_video("old", hours_old=49),
        make_video("off", title="Otra cosa"),
    ]
    summarizer = FakeSummarizer()
    channel = make_channel(title_filter=("TÍTULO",))
    deps = make_deps(tmp_path, feed(*videos), summarizer, FakeSender([False]), (channel,))

    await run(deps)

    assert summarizer.summarize_calls == []


async def test_processes_videos_oldest_first(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer = FakeSummarizer()
    fetch = fake_fetch(
        {
            "Chan": [make_video("new", hours_old=1), make_video("mid", hours_old=5)],
            "Other": [make_video("old", hours_old=10, channel_name="Other")],
        }
    )
    channels = (make_channel(), make_channel("Other"))
    deps = make_deps(tmp_path, fetch, summarizer, channels=channels)

    await run(deps)

    assert [url[-3:] for url in summarizer.summarize_calls] == ["old", "mid", "new"]


async def test_video_not_ready_stays_unseen(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer = FakeSummarizer(default=VideoNotReadyError("premiere"))
    sender = FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a")), summarizer, sender)

    result = await run(deps)

    assert result.deferred == 1 and result.failed == 0 and sender.texts == []
    state = load(tmp_path)
    assert not state.is_seen("a") and state.pending == {}
    assert state.record_summarize_failure("a", NOW) == 1


async def test_summarize_auth_failure_stops_iteration(tmp_path: Path) -> None:
    seed_state(tmp_path)
    summarizer = FakeSummarizer(default=NotebookAuthError("expired"))
    sender = FakeSender()
    videos = feed(make_video("a", hours_old=2), make_video("b"))
    deps = make_deps(tmp_path, videos, summarizer, sender)

    result = await run(deps)

    assert result.auth_failed and len(summarizer.summarize_calls) == 1
    assert sender.texts == [format_auth_alert()]
    state = load(tmp_path)
    assert not state.is_seen("a") and not state.is_seen("b") and state.auth_alert_sent


async def test_summarizer_error_gives_up_after_three_failures(tmp_path: Path) -> None:
    seed_state(tmp_path)
    bad, good = make_video("bad", hours_old=3), make_video("good")
    summarizer = FakeSummarizer(results={bad.url: SummarizerError("timeout")})
    deps = make_deps(tmp_path, feed(bad, good), summarizer)

    first = await run(deps)
    assert first.failed == 1 and first.sent == 1 and not load(tmp_path).is_seen("bad")
    await run(deps)
    third = await run(deps)
    assert third.given_up == 1 and load(tmp_path).is_seen("bad")
    await run(deps)

    assert summarizer.summarize_calls == [bad.url, good.url, bad.url, bad.url]


async def test_empty_answer_counts_as_summarize_failure(tmp_path: Path) -> None:
    seed_state(tmp_path)
    sender = FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a")), FakeSummarizer(default="  "), sender)

    result = await run(deps)

    assert result.failed == 1 and sender.texts == []
    state = load(tmp_path)
    assert not state.is_seen("a") and state.record_summarize_failure("a", NOW) == 2


async def test_failed_feed_does_not_block_other_channels(tmp_path: Path) -> None:
    seed_state(tmp_path)
    fetch = fake_fetch(
        {"Chan": FeedError("HTTP 500"), "Other": [make_video("a", channel_name="Other")]}
    )
    channels = (make_channel(), make_channel("Other"))
    deps = make_deps(tmp_path, fetch, channels=channels)

    result = await run(deps)

    assert result.feed_errors == 1 and result.sent == 1


async def test_telegram_failure_keeps_summary_and_never_resummarises(tmp_path: Path) -> None:
    seed_state(tmp_path)
    long_answer = '{"summary": "' + "Una frase de relleno bastante larga. " * 150 + '"}'
    summarizer = FakeSummarizer(default=long_answer)
    fetch = feed(make_video("a"))
    first_sender = FakeSender([True, False])

    await run(make_deps(tmp_path, fetch, summarizer, first_sender))

    pending = load(tmp_path).pending["a"]
    assert len(first_sender.texts) == 2 and pending.send_attempts == 1
    assert pending.chunks == [first_sender.texts[1]]
    second, third = FakeSender([False]), FakeSender([False])
    await run(make_deps(tmp_path, fetch, summarizer, second))
    result = await run(make_deps(tmp_path, fetch, summarizer, third))

    state = load(tmp_path)
    assert state.is_seen("a") and state.pending == {} and result.given_up == 1
    assert len(summarizer.summarize_calls) == 1
    first_chunk = first_sender.texts[0]
    assert (first_sender.texts + second.texts + third.texts).count(first_chunk) == 1
    assert second.texts == third.texts == [first_sender.texts[1]]


async def test_pending_is_retried_and_sent(tmp_path: Path) -> None:
    state = seed_state(tmp_path)
    state.pending["p"] = PendingMessage("Chan — Antiguo", ["resto"], send_attempts=1)
    state.save()
    summarizer, sender = FakeSummarizer(), FakeSender([True])
    deps = make_deps(tmp_path, feed(), summarizer, sender)

    result = await run(deps)

    assert result.retried == 1 and result.sent == 0 and sender.texts == ["resto"]
    assert summarizer.summarize_calls == []
    loaded = load(tmp_path)
    assert loaded.is_seen("p") and loaded.pending == {}


async def test_stop_event_prevents_new_videos(tmp_path: Path) -> None:
    state = seed_state(tmp_path)
    state.pending["p"] = PendingMessage("Chan — Antiguo", ["resto"])
    state.save()
    summarizer, sender = FakeSummarizer(), FakeSender()
    deps = make_deps(tmp_path, feed(make_video("a")), summarizer, sender)
    state.mark_seen("stale", NOW - timedelta(hours=73))
    state.save()
    deps.stop.set()

    await run(deps)

    assert summarizer.summarize_calls == [] and sender.texts == []
    saved = load(tmp_path)
    assert "p" in saved.pending and not saved.is_seen("stale")


async def test_cycle_prunes_old_seen_entries(tmp_path: Path) -> None:
    state = seed_state(tmp_path, seen=("recent",))
    state.mark_seen("old", NOW - timedelta(hours=73))
    state.mark_seen("edge", NOW - timedelta(hours=71))
    state.save()

    await run(make_deps(tmp_path, feed()), within_hours=False)

    pruned = load(tmp_path)
    assert not pruned.is_seen("old") and pruned.is_seen("edge") and pruned.is_seen("recent")


async def test_summary_is_saved_as_pending_before_first_send(tmp_path: Path) -> None:
    seed_state(tmp_path)
    on_disk: list[list[str]] = []

    class CheckingSender(FakeSender):
        async def send(self, text: str) -> bool:
            on_disk.append(load(tmp_path).pending["a"].chunks)
            return await super().send(text)

    sender = CheckingSender()
    await run(make_deps(tmp_path, feed(make_video("a")), sender=sender))

    assert on_disk == [sender.texts]


async def test_logs_outcome_by_label_without_message_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seed_state(tmp_path)
    caplog.set_level(logging.INFO, logger="youtube_summary.cycle")

    await run(make_deps(tmp_path, feed(make_video("a"))))

    assert "Chan — Título" in caplog.text and "Resumen corto." not in caplog.text


async def test_successful_summary_clears_failure_count_before_sending(tmp_path: Path) -> None:
    state = seed_state(tmp_path)
    state.record_summarize_failure("a", NOW)
    state.save()

    await run(make_deps(tmp_path, feed(make_video("a")), sender=FakeSender([False])))

    saved = load(tmp_path)
    assert "a" in saved.pending and saved.record_summarize_failure("a", NOW) == 1
