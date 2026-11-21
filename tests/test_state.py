import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from youtube_summary import state as state_module
from youtube_summary.state import PendingMessage, SeenState, UnsupportedStateVersionError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_load_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert SeenState.load(tmp_path / "seen.json") is None


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    state = SeenState.empty(path)
    state.mark_seen("a", NOW)
    state.mark_seen("b", NOW - timedelta(hours=1))
    state.pending["a"] = PendingMessage("Chan — Title", ["one", "two"], send_attempts=1)
    state.record_summarize_failure("c", NOW)
    state.auth_alert_sent = True
    state.save()

    loaded = SeenState.load(path)

    assert loaded is not None
    assert loaded.is_seen("a") and loaded.is_seen("b") and not loaded.is_seen("c")
    assert loaded.pending == {"a": PendingMessage("Chan — Title", ["one", "two"], 1)}
    assert loaded.record_summarize_failure("c", NOW) == 2
    assert loaded.auth_alert_sent is True


def test_prune_drops_entries_older_than_horizon(tmp_path: Path) -> None:
    state = SeenState.empty(tmp_path / "seen.json")
    state.mark_seen("old", NOW - timedelta(hours=73))
    state.mark_seen("new", NOW - timedelta(hours=1))
    state.record_summarize_failure("old", NOW - timedelta(hours=73))

    state.prune(NOW, timedelta(hours=72))

    assert not state.is_seen("old") and state.is_seen("new")
    assert state.record_summarize_failure("old", NOW) == 1


def test_record_summarize_failure_counts_up_and_clears(tmp_path: Path) -> None:
    state = SeenState.empty(tmp_path / "seen.json")

    assert state.record_summarize_failure("v", NOW) == 1
    assert state.record_summarize_failure("v", NOW) == 2
    state.clear_summarize_failure("v")
    assert state.record_summarize_failure("v", NOW) == 1


def test_naive_datetime_is_rejected(tmp_path: Path) -> None:
    state = SeenState.empty(tmp_path / "seen.json")

    with pytest.raises(ValueError):
        state.mark_seen("v", datetime(2026, 11, 20, 12, 0))


@pytest.mark.parametrize("content", ["{not json", ""])
def test_load_quarantines_corrupt_or_empty_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "seen.json"
    path.write_text(content, encoding="utf-8")

    assert SeenState.load(path) is None
    assert not path.exists()
    assert len(list(tmp_path.glob("seen.json.corrupt-*"))) == 1


VALID = '"seen": {}, "pending": {}, "summarize_failures": {}, "auth_alert_sent": false'


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2]",
        '{"version": 1, "seen": {}, "pending": {}, "auth_alert_sent": false}',
        '{"version": 1, "seen": [], "pending": {}, "summarize_failures": {},'
        ' "auth_alert_sent": false}',
        '{"version": 1, "seen": {"a": "not a date"}, "pending": {},'
        ' "summarize_failures": {}, "auth_alert_sent": false}',
        '{"version": 1, "seen": {}, "pending": {"a": {"label": 1, "chunks": [],'
        ' "send_attempts": 0}}, "summarize_failures": {}, "auth_alert_sent": false}',
        '{"version": true, ' + VALID + "}",
        '{"version": 1.0, ' + VALID + "}",
        "{" + VALID + "}",
        '{"version": 1, "seen": {"a": "0001-01-01T00:00:00+01:00"}, "pending": {},'
        ' "summarize_failures": {}, "auth_alert_sent": false}',
    ],
)
def test_load_quarantines_wrong_shape(tmp_path: Path, content: str) -> None:
    path = tmp_path / "seen.json"
    path.write_text(content, encoding="utf-8")

    assert SeenState.load(path) is None
    assert not path.exists()
    assert len(list(tmp_path.glob("seen.json.corrupt-*"))) == 1


def test_save_leaves_no_tmp_file_and_overwrites_stale_tmp(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "seen.json"
    path.parent.mkdir()
    (path.parent / "seen.json.tmp").write_text("stale", encoding="utf-8")

    SeenState.empty(path).save()

    assert sorted(p.name for p in path.parent.iterdir()) == ["seen.json"]
    assert SeenState.load(path) is not None


def test_unknown_integer_version_fails_loudly_and_keeps_file(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text('{"version": 2, ' + VALID + "}", encoding="utf-8")

    with pytest.raises(UnsupportedStateVersionError, match=r"seen\.json.*2"):
        SeenState.load(path)
    assert path.exists()
    assert not list(tmp_path.glob("seen.json.corrupt-*"))


def test_quarantine_never_overwrites_earlier_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_module, "_utcnow", lambda: NOW)
    path = tmp_path / "seen.json"
    for index in range(3):
        path.write_text(f"{{bad {index}", encoding="utf-8")
        assert SeenState.load(path) is None

    names = sorted(p.name for p in tmp_path.iterdir())
    base = "seen.json.corrupt-20261120T120000Z"
    assert names == [base, f"{base}-1", f"{base}-2"]
    assert (tmp_path / base).read_text(encoding="utf-8") == "{bad 0"


def test_save_failure_keeps_original_and_removes_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "seen.json"
    state = SeenState.empty(path)
    state.mark_seen("kept", NOW)
    state.save()
    original = path.read_text(encoding="utf-8")
    state.mark_seen("new", NOW)

    def boom(_fd: int) -> None:
        raise OSError("disk")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk"):
        state.save()

    assert path.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["seen.json"]


def test_save_fsyncs_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[bool] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    SeenState.empty(tmp_path / "seen.json").save()

    assert synced == [False, True]
