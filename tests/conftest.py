from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from youtube_summary.config import ActiveHours, AppConfig, Channel
from youtube_summary.feed import Video

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ANSWER = '{"summary": "Resumen corto.", "key_points": ["Punto"]}'


def make_video(
    video_id: str,
    *,
    hours_old: float = 1,
    title: str = "Título",
    channel_name: str = "Chan",
    url: str | None = None,
) -> Video:
    return Video(
        video_id=video_id,
        url=url or f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        published_at=NOW - timedelta(hours=hours_old),
        channel_name=channel_name,
    )


def make_config(**overrides: Any) -> AppConfig:
    values: dict[str, Any] = {
        "notebook_id": "nb",
        "poll_interval_minutes": 60,
        "active_hours": ActiveHours(7, 23, ZoneInfo("Europe/Madrid")),
        "max_video_age_hours": 48,
        "prompt": "Resume el vídeo",
        "language": "en",
    }
    values.update(overrides)
    return AppConfig(**values)


def make_channel(name: str = "Chan", title_filter: tuple[str, ...] = ()) -> Channel:
    return Channel(name=name, channel_id=f"UC{name}", title_filter=title_filter)


class FakeSummarizer:
    def __init__(
        self,
        results: dict[str, str | Exception] | None = None,
        default: str | Exception = ANSWER,
        ping_error: Exception | None = None,
    ) -> None:
        self.results = results or {}
        self.default = default
        self.ping_error = ping_error
        self.summarize_calls: list[str] = []
        self.ping_calls = 0

    async def summarize(self, video_url: str, prompt: str) -> str:
        self.summarize_calls.append(video_url)
        result = self.results.get(video_url, self.default)
        if isinstance(result, Exception):
            raise result
        return result

    async def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error


class FakeSender:
    def __init__(self, results: list[bool] | None = None) -> None:
        self.results = list(results or [])
        self.texts: list[str] = []

    async def send(self, text: str) -> bool:
        self.texts.append(text)
        return self.results.pop(0) if self.results else True


class FakeFetch:
    def __init__(self, mapping: dict[str, list[Video] | Exception]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, channel: Channel) -> list[Video]:
        self.calls.append(channel.name)
        result = self.mapping.get(channel.name, [])
        if isinstance(result, Exception):
            raise result
        return result


def fake_fetch(mapping: dict[str, list[Video] | Exception]) -> FakeFetch:
    return FakeFetch(mapping)
