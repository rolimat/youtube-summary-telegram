from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from youtube_summary.config import Channel
from youtube_summary.feed import (
    FEED_URL_TEMPLATE,
    FeedError,
    fetch_channel_videos,
    matches_title_filter,
    parse_feed,
)

CHANNEL = Channel(name="Example", channel_id="UC1", title_filter=())
FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_text(encoding="utf-8")


def test_parse_feed_returns_complete_entries() -> None:
    videos = parse_feed(FIXTURE, "Example")

    assert [v.video_id for v in videos] == ["abc123", "def456"]
    assert [v.title for v in videos] == ["Hablemos de bolsa", "Vlog semanal"]
    assert videos[0].url == "https://www.youtube.com/watch?v=abc123"
    assert videos[0].channel_name == "Example"
    assert videos[0].published_at == datetime(2026, 11, 20, 10, 30, tzinfo=UTC)
    assert videos[1].published_at == datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    assert videos[1].published_at.tzinfo is UTC


def test_parse_feed_raises_feed_error_on_malformed_xml() -> None:
    with pytest.raises(FeedError):
        parse_feed("<feed><entry>", "Example")


def _feed(published: str, href: str = "https://youtu.be/x") -> str:
    return (
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns="http://www.w3.org/2005/Atom"><entry><yt:videoId>x</yt:videoId>'
        f'<link rel="alternate" href="{href}"/><published>{published}</published>'
        "</entry></feed>"
    )


@pytest.mark.parametrize(
    "xml",
    [
        _feed("not-a-date"),
        _feed("2026-11-20T10:30:00"),
        _feed("2026-11-20T10:30:00+00:00", href=""),
    ],
)
def test_parse_feed_skips_entries_with_bad_date_naive_date_or_empty_link(xml: str) -> None:
    assert parse_feed(xml, "Example") == []


def test_parse_feed_missing_title_becomes_empty() -> None:
    assert parse_feed(_feed("2026-11-20T10:30:00+00:00"), "Example")[0].title == ""


async def test_fetch_channel_videos_raises_on_404() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(FeedError, match="Example"):
            await fetch_channel_videos(http, CHANNEL)


async def test_fetch_channel_videos_raises_on_transport_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(FeedError, match="Example"):
            await fetch_channel_videos(http, CHANNEL)


async def test_fetch_channel_videos_requests_channel_feed_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        videos = await fetch_channel_videos(http, CHANNEL)

    assert seen == [FEED_URL_TEMPLATE.format(channel_id="UC1")]
    assert len(videos) == 2


@pytest.mark.parametrize(
    ("title", "words", "expected"),
    [
        ("Hablemos de BOLSA", ("bolsa",), True),
        ("Vlog", ("bolsa",), False),
        ("Vlog", (), True),
        ("INFLACIÓN y tipos", ("inflación", "fed"), True),
    ],
)
def test_matches_title_filter(title: str, words: tuple[str, ...], expected: bool) -> None:
    assert matches_title_filter(title, words) is expected
