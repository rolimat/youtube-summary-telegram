import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

VERSION = 1
logger = logging.getLogger(__name__)


@dataclass
class PendingMessage:
    label: str
    chunks: list[str]
    send_attempts: int = 0


@dataclass
class _Failure:
    count: int
    at: datetime


class StateCorruptError(ValueError): ...


class UnsupportedStateVersionError(Exception): ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(UTC)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise StateCorruptError("timestamp must be a string")
    try:
        return _utc(datetime.fromisoformat(value))
    except ValueError as error:
        raise StateCorruptError(f"bad timestamp {value!r}") from error


def _pending_from(raw: object) -> PendingMessage:
    if not isinstance(raw, dict):
        raise StateCorruptError("pending entry must be an object")
    label, chunks, attempts = raw.get("label"), raw.get("chunks"), raw.get("send_attempts")
    if not (isinstance(chunks, list) and all(isinstance(c, str) for c in chunks)):
        raise StateCorruptError("ill-typed pending entry")
    if not (isinstance(label, str) and type(attempts) is int):
        raise StateCorruptError("ill-typed pending entry")
    return PendingMessage(label, list(chunks), attempts)


def _failure_from(raw: object) -> _Failure:
    if not isinstance(raw, dict) or type(raw.get("count")) is not int:
        raise StateCorruptError("ill-typed failure entry")
    return _Failure(raw["count"], _parse_time(raw.get("at")))


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise StateCorruptError(f"{name} must be an object")
    return section


def _check_version(path: Path, data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StateCorruptError("state document must be an object")
    version = data.get("version")
    if type(version) is not int:
        raise StateCorruptError("version must be an integer")
    if version != VERSION:
        raise UnsupportedStateVersionError(f"{path} has unsupported state version {version}")
    return data


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_CORRUPT_ERRORS = (
    json.JSONDecodeError,
    UnicodeDecodeError,
    OverflowError,
    RecursionError,
    StateCorruptError,
)


class SeenState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: dict[str, datetime] = {}
        self._failures: dict[str, _Failure] = {}
        self.pending: dict[str, PendingMessage] = {}
        self.auth_alert_sent = False

    @classmethod
    def empty(cls, path: Path) -> SeenState:
        return cls(path)

    @classmethod
    def load(cls, path: Path) -> SeenState | None:
        if not path.exists():
            return None
        try:
            return cls._parse(path, json.loads(path.read_text(encoding="utf-8")))
        except _CORRUPT_ERRORS as error:
            cls._quarantine(path, error)
            return None

    @classmethod
    def _parse(cls, path: Path, data: object) -> SeenState:
        data = _check_version(path, data)
        state = cls(path)
        state._seen = {k: _parse_time(v) for k, v in _section(data, "seen").items()}
        state.pending = {k: _pending_from(v) for k, v in _section(data, "pending").items()}
        failures = _section(data, "summarize_failures")
        state._failures = {k: _failure_from(v) for k, v in failures.items()}
        alert = data.get("auth_alert_sent")
        if not isinstance(alert, bool):
            raise StateCorruptError("auth_alert_sent must be a boolean")
        state.auth_alert_sent = alert
        return state

    @staticmethod
    def _quarantine(path: Path, error: Exception) -> None:
        base = f"{path.name}.corrupt-{_utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        target = path.with_name(base)
        suffix = 0
        while target.exists():
            suffix += 1
            target = path.with_name(f"{base}-{suffix}")
        path.rename(target)
        logger.error("State file %s is corrupt (%s); moved to %s", path, error, target)

    def is_seen(self, video_id: str) -> bool:
        return video_id in self._seen

    def mark_seen(self, video_id: str, at: datetime) -> None:
        self._seen[video_id] = _utc(at)

    def record_summarize_failure(self, video_id: str, at: datetime) -> int:
        previous = self._failures.get(video_id)
        count = 1 if previous is None else previous.count + 1
        self._failures[video_id] = _Failure(count, _utc(at))
        return count

    def clear_summarize_failure(self, video_id: str) -> None:
        self._failures.pop(video_id, None)

    def prune(self, now: datetime, horizon: timedelta) -> None:
        cutoff = _utc(now) - horizon
        self._seen = {k: at for k, at in self._seen.items() if at >= cutoff}
        self._failures = {k: f for k, f in self._failures.items() if f.at >= cutoff}

    def save(self) -> None:
        document = {
            "version": VERSION,
            "seen": {k: at.isoformat() for k, at in self._seen.items()},
            "pending": {k: asdict(m) for k, m in self.pending.items()},
            "summarize_failures": {
                k: {"count": f.count, "at": f.at.isoformat()} for k, f in self._failures.items()
            },
            "auth_alert_sent": self.auth_alert_sent,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(document, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self._path)
        _fsync_directory(self._path.parent)
