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


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(UTC)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    return _utc(datetime.fromisoformat(value))


def _pending_from(raw: object) -> PendingMessage:
    if not isinstance(raw, dict):
        raise ValueError("pending entry must be an object")
    label, chunks, attempts = raw["label"], raw["chunks"], raw["send_attempts"]
    valid_chunks = isinstance(chunks, list) and all(isinstance(c, str) for c in chunks)
    if not (isinstance(label, str) and valid_chunks and type(attempts) is int):
        raise ValueError("ill-typed pending entry")
    return PendingMessage(label, list(chunks), attempts)


def _failure_from(raw: object) -> _Failure:
    if not isinstance(raw, dict) or type(raw["count"]) is not int:
        raise ValueError("ill-typed failure entry")
    return _Failure(raw["count"], _parse_time(raw["at"]))


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data[name]
    if not isinstance(section, dict):
        raise ValueError(f"{name} must be an object")
    return section


class SeenState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: dict[str, datetime] = {}
        self._failures: dict[str, _Failure] = {}
        self.pending: dict[str, PendingMessage] = {}
        self.auth_alert_sent = False

    @classmethod
    def empty(cls, path: Path) -> "SeenState":
        return cls(path)

    @classmethod
    def load(cls, path: Path) -> "SeenState | None":
        if not path.exists():
            return None
        try:
            return cls._parse(path, json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, KeyError, TypeError) as error:
            cls._quarantine(path, error)
            return None

    @classmethod
    def _parse(cls, path: Path, data: object) -> "SeenState":
        if not isinstance(data, dict) or data["version"] != VERSION:
            raise ValueError("unsupported state document")
        state = cls(path)
        state._seen = {k: _parse_time(v) for k, v in _section(data, "seen").items()}
        state.pending = {k: _pending_from(v) for k, v in _section(data, "pending").items()}
        failures = _section(data, "summarize_failures")
        state._failures = {k: _failure_from(v) for k, v in failures.items()}
        if not isinstance(data["auth_alert_sent"], bool):
            raise ValueError("auth_alert_sent must be a boolean")
        state.auth_alert_sent = data["auth_alert_sent"]
        return state

    @staticmethod
    def _quarantine(path: Path, error: Exception) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = path.with_name(f"{path.name}.corrupt-{stamp}")
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
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._path)
