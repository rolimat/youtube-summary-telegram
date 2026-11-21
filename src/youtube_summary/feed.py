from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree

import httpx

from youtube_summary.config import Channel

ATOM_NS = "http://www.w3.org/2005/Atom"
FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
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
    title = _text(entry, f"{{{ATOM_NS}}}title") or ""
    return Video(video_id, url, title, published.astimezone(UTC), channel_name)


def parse_feed(xml_text: str, channel_name: str) -> list[Video]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise FeedError(f"malformed feed XML for {channel_name}: {error}") from error
    entries = (_parse_entry(e, channel_name) for e in root.findall(f"{{{ATOM_NS}}}entry"))
    return [video for video in entries if video is not None]


async def fetch_channel_videos(http: httpx.AsyncClient, channel: Channel) -> list[Video]:
    url = FEED_URL_TEMPLATE.format(channel_id=channel.channel_id)
    try:
        response = await http.get(url, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise FeedError(f"feed fetch failed for {channel.name}: {error!r}") from error
    return parse_feed(response.text, channel.name)


def matches_title_filter(title: str, words: tuple[str, ...]) -> bool:
    folded = title.casefold()
    return not words or any(word.casefold() in folded for word in words)
