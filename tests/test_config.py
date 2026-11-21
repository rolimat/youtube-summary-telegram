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
from youtube_summary.texts import ENGLISH, SPANISH

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


def test_load_app_config_rejects_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "latin.yml"
    path.write_bytes(b"prompt: caf\xe9")

    with pytest.raises(ConfigError, match=r"latin\.yml"):
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


def test_load_channels_treats_null_title_filter_as_empty(tmp_path: Path) -> None:
    entry = {"name": "A", "channel_id": "UC1", "title_filter": None}
    path = write_yaml(tmp_path, {"channels": [entry]}, "channels.yml")

    assert load_channels(path)[0].title_filter == ()


def test_load_channels_strips_words_and_rejects_blank_ones(tmp_path: Path) -> None:
    entry: dict[str, Any] = {"name": "A", "channel_id": "UC1", "title_filter": [" bolsa "]}
    path = write_yaml(tmp_path, {"channels": [entry]}, "channels.yml")
    assert load_channels(path)[0].title_filter == ("bolsa",)

    entry["title_filter"] = ["bolsa", "  "]
    path = write_yaml(tmp_path, {"channels": [entry]}, "channels.yml")
    with pytest.raises(ConfigError, match=r"channels\[0\]\.title_filter"):
        load_channels(path)


def test_load_channels_strips_ids_and_names(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path, {"channels": [{"name": " A ", "channel_id": " UC1 "}]}, "channels.yml"
    )

    (channel,) = load_channels(path)

    assert (channel.name, channel.channel_id) == ("A", "UC1")


def test_load_channels_rejects_empty_list(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, {"channels": []}, "channels.yml")

    with pytest.raises(ConfigError, match="channels"):
        load_channels(path)


def test_load_channels_rejects_channel_without_channel_id(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, {"channels": [{"name": "A"}]}, "channels.yml")

    with pytest.raises(ConfigError, match="channel_id"):
        load_channels(path)


def test_load_channels_rejects_duplicate_channel_id(tmp_path: Path) -> None:
    entries = [{"name": "A", "channel_id": "UC1"}, {"name": "B", "channel_id": "UC1 "}]
    path = write_yaml(tmp_path, {"channels": entries}, "channels.yml")

    with pytest.raises(ConfigError, match="UC1"):
        load_channels(path)


ENV = {
    "TELEGRAM_BOT_TOKEN": "t",
    "TELEGRAM_CHAT_ID": "c",
    "NOTEBOOKLM_STORAGE_STATE_PATH": "/secrets/storage_state.json",
}


@pytest.mark.parametrize("missing", sorted(ENV))
def test_load_settings_requires_every_required_variable(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}

    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


@pytest.mark.parametrize("blank", sorted(ENV))
def test_load_settings_rejects_whitespace_only_variable(blank: str) -> None:
    with pytest.raises(ConfigError, match=blank):
        load_settings({**ENV, blank: "   "})


def test_load_settings_applies_dir_defaults() -> None:
    settings = load_settings(ENV)

    assert settings.config_dir == Path("/config")
    assert settings.state_dir == Path("/state")
    assert settings.storage_state_path == Path("/secrets/storage_state.json")


def test_load_settings_blank_dirs_fall_back_to_defaults() -> None:
    settings = load_settings({**ENV, "CONFIG_DIR": "", "STATE_DIR": "  "})

    assert (settings.config_dir, settings.state_dir) == (Path("/config"), Path("/state"))


def test_load_settings_honours_dir_overrides() -> None:
    settings = load_settings({**ENV, "CONFIG_DIR": "/c", "STATE_DIR": "/s"})

    assert (settings.config_dir, settings.state_dir) == (Path("/c"), Path("/s"))


def test_sample_files_load() -> None:
    config = load_app_config(ROOT / "config.yml.sample")
    channels = load_channels(ROOT / "channels.yml.sample")

    assert config.poll_interval_minutes == 15
    assert config.max_video_age_hours == 48
    assert len(channels) == 2


def test_language_defaults_to_english(tmp_path: Path) -> None:
    assert load_app_config(write_yaml(tmp_path, VALID)).texts is ENGLISH


def test_language_spanish_is_accepted(tmp_path: Path) -> None:
    assert load_app_config(write_yaml(tmp_path, {**VALID, "language": "es"})).texts is SPANISH


def test_language_unknown_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="language"):
        load_app_config(write_yaml(tmp_path, {**VALID, "language": "fr"}))


@pytest.mark.parametrize("value", ["ES", " es "])
def test_language_is_stripped_and_case_insensitive(tmp_path: Path, value: str) -> None:
    assert load_app_config(write_yaml(tmp_path, {**VALID, "language": value})).texts is SPANISH


@pytest.mark.parametrize("value", [["es"], {"a": 1}, 5, None])
def test_language_non_string_is_rejected(tmp_path: Path, value: Any) -> None:
    with pytest.raises(ConfigError, match="language"):
        load_app_config(write_yaml(tmp_path, {**VALID, "language": value}))
