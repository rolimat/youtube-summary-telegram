import asyncio
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree

import httpx

from youtube_summary.config import Channel

ATOM_NS = "http://www.w3.org/2005/Atom"
FEED_URL = "https://www.youtube.com/feeds/videos.xml"
FETCH_TIMEOUT_SECONDS = 15.0
YT_NS = "http://www.youtube.com/xml/schemas/2015"


class FeedError(Exception): ...


@dataclass(frozen=True)
class Video:
    video_id: str
    url: str
    title: str
    published_at: datetime
    channel_name: str


def _text(entry: ElementTree.Element, tag: str) -> str | None:
    element = entry.find(tag)
    return None if element is None or element.text is None else element.text.strip()


def _parse_entry(entry: ElementTree.Element, channel_name: str) -> Video | None:
    video_id = _text(entry, f"{{{YT_NS}}}videoId")
    published_raw = _text(entry, f"{{{ATOM_NS}}}published")
    link = entry.find(f"{{{ATOM_NS}}}link[@rel='alternate']")
    url = "" if link is None else link.attrib.get("href", "")
    if not video_id or not published_raw or not url:
        return None
    try:
        published = datetime.fromisoformat(published_raw)
    except ValueError:
        return None
    if published.tzinfo is None:
        return None
    try:
        published_utc = published.astimezone(UTC)
    except OverflowError:
        return None
    title = _text(entry, f"{{{ATOM_NS}}}title") or ""
    return Video(video_id, url, title, published_utc, channel_name)


def parse_feed(xml_text: str | bytes, channel_name: str) -> list[Video]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise FeedError(f"malformed feed XML for {channel_name}: {error}") from error
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise FeedError(f"not an Atom feed for {channel_name}")
    entries = (_parse_entry(e, channel_name) for e in root.findall(f"{{{ATOM_NS}}}entry"))
    return [video for video in entries if video is not None]


async def fetch_channel_videos(http: httpx.AsyncClient, channel: Channel) -> list[Video]:
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            response = await http.get(
                FEED_URL,
                params={"channel_id": channel.channel_id},
                timeout=FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError) as error:
        raise FeedError(f"feed fetch failed for {channel.name}: {error!r}") from error
    return parse_feed(response.content, channel.name)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def matches_title_filter(title: str, words: tuple[str, ...]) -> bool:
    folded = _normalise(title)
    return not words or any(_normalise(word) in folded for word in words)


def watch_url(video: Video) -> str:
    return f"https://www.youtube.com/watch?v={video.video_id}"


def is_short(video: Video) -> bool:
    return "/shorts/" in video.url
