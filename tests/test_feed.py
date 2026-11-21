import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import make_video

from youtube_summary import feed as feed_module
from youtube_summary.config import Channel
from youtube_summary.feed import (
    FEED_URL,
    FeedError,
    fetch_channel_videos,
    is_short,
    matches_title_filter,
    parse_feed,
    watch_url,
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
        _feed("0001-01-01T00:00:00+05:00"),
        _feed("2026-11-20T10:30:00+00:00").replace("<yt:videoId>x<", "<yt:videoId>  <"),
        _feed("2026-11-20T10:30:00+00:00").replace(' rel="alternate"', ' rel="other"'),
        _feed("2026-11-20T10:30:00+00:00").replace("published>", "updated>"),
    ],
)
def test_parse_feed_skips_entries_with_bad_date_naive_date_or_empty_link(xml: str) -> None:
    assert parse_feed(xml, "Example") == []


def test_parse_feed_raises_feed_error_when_root_is_not_atom_feed() -> None:
    with pytest.raises(FeedError, match="Example"):
        parse_feed("<html><body>consent</body></html>", "Example")


def test_parse_feed_accepts_bytes_with_declared_encoding() -> None:
    xml = _feed("2026-11-20T10:30:00+00:00").replace("<entry>", "<entry><title>Año</title>")
    data = ('<?xml version="1.0" encoding="ISO-8859-1"?>' + xml).encode("latin-1")

    assert parse_feed(data, "Example")[0].title == "Año"


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
    tricky = Channel(name="Example", channel_id="UC/1&x", title_filter=())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        videos = await fetch_channel_videos(http, tricky)

    assert seen == [f"{FEED_URL}?channel_id=UC%2F1%26x"]
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


def test_matches_title_filter_normalises_unicode() -> None:
    assert matches_title_filter("Inflacio\u0301n hoy", ("inflaci\u00f3n",))


async def test_fetch_channel_videos_enforces_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, text=FIXTURE)

    monkeypatch.setattr(feed_module, "FETCH_TIMEOUT_SECONDS", 0.01)
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        with pytest.raises(FeedError, match="Example"):
            await fetch_channel_videos(http, CHANNEL)


async def test_fetch_channel_videos_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.youtube.com":
            return httpx.Response(302, headers={"location": "https://feeds.example/f"})
        return httpx.Response(200, text=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert len(await fetch_channel_videos(http, CHANNEL)) == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/shorts/abc", True),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://youtu.be/abc", False),
    ],
)
def test_is_short(url: str, expected: bool) -> None:
    assert is_short(make_video("abc", url=url)) is expected


def test_watch_url_is_canonical() -> None:
    short = make_video("abc", url="https://www.youtube.com/shorts/abc")

    assert watch_url(short) == "https://www.youtube.com/watch?v=abc"
