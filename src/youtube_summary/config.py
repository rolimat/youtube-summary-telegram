from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from youtube_summary.texts import DEFAULT_LANGUAGE, TEXTS


class ConfigError(Exception): ...


@dataclass(frozen=True)
class Channel:
    name: str
    channel_id: str
    title_filter: tuple[str, ...]


@dataclass(frozen=True)
class ActiveHours:
    start: int
    end: int
    timezone: ZoneInfo


@dataclass(frozen=True)
class AppConfig:
    notebook_id: str
    poll_interval_minutes: int
    active_hours: ActiveHours
    max_video_age_hours: int
    prompt: str
    language: str


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    storage_state_path: Path
    config_dir: Path
    state_dir: Path


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ConfigError(f"{path}: cannot read file: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level document must be a mapping")
    return data


def _section(path: Path, parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: '{key}' must be a mapping")
    return value


def _text(path: Path, parent: Mapping[str, Any], key: str, label: str | None = None) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: '{label or key}' must be a non-empty string")
    return value.strip()


def _positive_int(path: Path, parent: Mapping[str, Any], key: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path}: '{key}' must be a positive integer")
    return value


def _hour(path: Path, parent: Mapping[str, Any], key: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 23:
        raise ConfigError(f"{path}: 'active_hours.{key}' must be an integer from 0 to 23")
    return value


def _active_hours(path: Path, data: Mapping[str, Any]) -> ActiveHours:
    hours = _section(path, data, "active_hours")
    start, end = _hour(path, hours, "start"), _hour(path, hours, "end")
    if start > end:
        raise ConfigError(f"{path}: 'active_hours' start must not be after end")
    name = _text(path, hours, "timezone", "active_hours.timezone")
    try:
        return ActiveHours(start, end, ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"{path}: 'active_hours.timezone' unknown timezone '{name}'") from error


def _language(path: Path, data: Mapping[str, Any]) -> str:
    value = data.get("language", DEFAULT_LANGUAGE)
    if value not in TEXTS:
        raise ConfigError(f"{path}: 'language' must be one of {', '.join(sorted(TEXTS))}")
    return str(value)


def load_app_config(path: Path) -> AppConfig:
    data = _load_mapping(path)
    notebook = _section(path, data, "notebooklm")
    return AppConfig(
        notebook_id=_text(path, notebook, "notebook_id", "notebooklm.notebook_id"),
        poll_interval_minutes=_positive_int(path, data, "poll_interval_minutes"),
        active_hours=_active_hours(path, data),
        max_video_age_hours=_positive_int(path, data, "max_video_age_hours"),
        prompt=_text(path, data, "prompt"),
        language=_language(path, data),
    )


def _channel(path: Path, index: int, entry: Any) -> Channel:
    if not isinstance(entry, dict):
        raise ConfigError(f"{path}: 'channels[{index}]' must be a mapping")
    where = f"channels[{index}]"
    filters = entry.get("title_filter") or []
    if not isinstance(filters, list) or not all(isinstance(f, str) and f.strip() for f in filters):
        raise ConfigError(f"{path}: '{where}.title_filter' must be a list of non-empty strings")
    return Channel(
        name=_text(path, entry, "name", f"{where}.name"),
        channel_id=_text(path, entry, "channel_id", f"{where}.channel_id"),
        title_filter=tuple(f.strip() for f in filters),
    )


def load_channels(path: Path) -> tuple[Channel, ...]:
    entries = _load_mapping(path).get("channels")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: 'channels' must be a non-empty list")
    channels = tuple(_channel(path, i, entry) for i, entry in enumerate(entries))
    seen: set[str] = set()
    for channel in channels:
        if channel.channel_id in seen:
            raise ConfigError(f"{path}: duplicate channel_id '{channel.channel_id}'")
        seen.add(channel.channel_id)
    return channels


def _required_env(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise ConfigError(f"environment: {key} is required")
    return value


def load_settings(environ: Mapping[str, str]) -> Settings:
    return Settings(
        telegram_bot_token=_required_env(environ, "TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required_env(environ, "TELEGRAM_CHAT_ID"),
        storage_state_path=Path(_required_env(environ, "NOTEBOOKLM_STORAGE_STATE_PATH")),
        config_dir=Path(environ.get("CONFIG_DIR", "").strip() or "/config"),
        state_dir=Path(environ.get("STATE_DIR", "").strip() or "/state"),
    )
