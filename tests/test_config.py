from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from youtube_summary.config import (
    ConfigError,
    load_app_config,
    load_channels,
    load_settings,
)

ROOT = Path(__file__).parent.parent

VALID: dict[str, Any] = {
    "notebooklm": {"notebook_id": "nb-1"},
    "poll_interval_minutes": 15,
    "active_hours": {"start": 7, "end": 23, "timezone": "Europe/Madrid"},
    "max_video_age_hours": 48,
    "prompt": "Return ONLY JSON",
}


def write_yaml(tmp_path: Path, data: Any, name: str = "config.yml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_app_config_reads_all_fields(tmp_path: Path) -> None:
    config = load_app_config(write_yaml(tmp_path, VALID))

    assert config.notebook_id == "nb-1"
    assert config.poll_interval_minutes == 15
    assert config.active_hours.start == 7
    assert config.active_hours.end == 23
    assert config.active_hours.timezone == ZoneInfo("Europe/Madrid")
    assert config.max_video_age_hours == 48
    assert config.prompt


def test_load_app_config_rejects_missing_notebook_id(tmp_path: Path) -> None:
    data = {**VALID, "notebooklm": {}}

    with pytest.raises(ConfigError, match=r"notebooklm\.notebook_id"):
        load_app_config(write_yaml(tmp_path, data))


def with_hours(**changes: Any) -> dict[str, Any]:
    return {**VALID, "active_hours": {**VALID["active_hours"], **changes}}


@pytest.mark.parametrize(
    ("data", "key"),
    [
        (with_hours(start=-1), "active_hours.start"),
        (with_hours(end=24), "active_hours.end"),
        (with_hours(start=20, end=10), "active_hours"),
        ({**VALID, "poll_interval_minutes": 0}, "poll_interval_minutes"),
        ({**VALID, "max_video_age_hours": -5}, "max_video_age_hours"),
        (with_hours(timezone="Mars/Olympus"), "active_hours.timezone"),
        ({**VALID, "prompt": "  "}, "prompt"),
    ],
)
def test_load_app_config_rejects_invalid_values(
    tmp_path: Path, data: dict[str, Any], key: str
) -> None:
    path = write_yaml(tmp_path, data)

    with pytest.raises(ConfigError, match=key) as error:
        load_app_config(path)
    assert str(path) in str(error.value)


def test_load_app_config_rejects_unreadable_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yml"
    broken.write_text("a: [unclosed", encoding="utf-8")
    listy = write_yaml(tmp_path, ["not", "a", "mapping"], "list.yml")

    for path in (tmp_path / "missing.yml", broken, listy):
        with pytest.raises(ConfigError, match=path.name):
            load_app_config(path)


def test_load_channels_defaults_title_filter_to_empty(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, {"channels": [{"name": "A", "channel_id": "UC1"}]}, "channels.yml")

    (channel,) = load_channels(path)

    assert channel.title_filter == ()
    assert channel.channel_id == "UC1"


def test_load_channels_reads_title_filter(tmp_path: Path) -> None:
    entry = {"name": "A", "channel_id": "UC1", "title_filter": ["bolsa"]}
    path = write_yaml(tmp_path, {"channels": [entry]}, "channels.yml")

    assert load_channels(path)[0].title_filter == ("bolsa",)


def test_load_channels_rejects_channel_without_channel_id(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, {"channels": [{"name": "A"}]}, "channels.yml")

    with pytest.raises(ConfigError, match="channel_id"):
        load_channels(path)


def test_load_channels_rejects_duplicate_channel_id(tmp_path: Path) -> None:
    entries = [{"name": "A", "channel_id": "UC1"}, {"name": "B", "channel_id": "UC1"}]
    path = write_yaml(tmp_path, {"channels": entries}, "channels.yml")

    with pytest.raises(ConfigError, match="UC1"):
        load_channels(path)


ENV = {
    "TELEGRAM_BOT_TOKEN": "t",
    "TELEGRAM_CHAT_ID": "c",
    "NOTEBOOKLM_STORAGE_STATE_PATH": "/secrets/storage_state.json",
}


@pytest.mark.parametrize("missing", sorted(ENV))
def test_load_settings_requires_telegram_token(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}

    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_load_settings_applies_dir_defaults() -> None:
    settings = load_settings(ENV)

    assert settings.config_dir == Path("/config")
    assert settings.state_dir == Path("/state")
    assert settings.storage_state_path == Path("/secrets/storage_state.json")


def test_load_settings_honours_dir_overrides() -> None:
    settings = load_settings({**ENV, "CONFIG_DIR": "/c", "STATE_DIR": "/s"})

    assert (settings.config_dir, settings.state_dir) == (Path("/c"), Path("/s"))


def test_sample_files_load() -> None:
    config = load_app_config(ROOT / "config.yml.sample")
    channels = load_channels(ROOT / "channels.yml.sample")

    assert config.poll_interval_minutes == 15
    assert config.max_video_age_hours == 48
    assert len(channels) == 2
