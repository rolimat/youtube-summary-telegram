import html
import json
import re
from dataclasses import dataclass

from youtube_summary.feed import Video
from youtube_summary.texts import Texts

_FENCE = re.compile(r"```[\w+-]*\s*(.*?)```", re.DOTALL)
_SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+")
_GAP = re.compile(r"\s+")
_PARTIAL_ENTITY = re.compile(r"&#?\w*$")
_CITATION = re.compile(r"\s*\[\d{1,2}(?:\s*[,\-\u2013]\s*\d{1,2})*\]")


class EmptyAnswerError(ValueError): ...


@dataclass(frozen=True)
class Summary:
    text: str
    key_points: tuple[str, ...]


def parse_answer(answer: str) -> Summary:
    fenced = _FENCE.search(answer)
    cleaned = _CITATION.sub("", fenced.group(1) if fenced else answer).strip()
    if not cleaned:
        raise EmptyAnswerError("empty answer")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return Summary(cleaned, ())
    text = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return Summary(cleaned, ())
    points = data.get("key_points")
    kept = [p.strip() for p in points if isinstance(p, str)] if isinstance(points, list) else []
    return Summary(text.strip(), tuple(p for p in kept if p))


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def format_message(
    video: Video, summary: Summary, texts: Texts, max_chars: int = 4096
) -> list[str]:
    title = video.title or texts.untitled
    header = f"🎬 <b>{_escape(video.channel_name)}</b> — {_escape(title)}"
    footer = f'🔗 <a href="{html.escape(video.url, quote=True)}">{_escape(texts.watch_video)}</a>'
    parts = [_escape(summary.text)]
    if summary.key_points:
        parts.append("\n".join(f"• {_escape(point)}" for point in summary.key_points))
    body = "\n\n".join(parts)
    whole = f"{header}\n\n{body}\n\n{footer}"
    if _units(whole) <= max_chars:
        return [whole]
    pieces = split_for_telegram(body, max_chars - _units(header) - _units(footer) - 4)
    pieces[0] = f"{header}\n\n{pieces[0]}"
    pieces[-1] = f"{pieces[-1]}\n\n{footer}"
    return pieces


def _fit(text: str, max_units: int) -> int:
    units = 0
    for index, char in enumerate(text):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > max_units:
            return index
    return len(text)


def _cut_point(window: str) -> tuple[int, int]:
    for pattern in (_SENTENCE_GAP, _GAP):
        gaps = [m for m in pattern.finditer(window) if m.start() > 0]
        if gaps:
            return gaps[-1].start(), gaps[-1].end()
    partial = _PARTIAL_ENTITY.search(window)
    cut = partial.start() if partial and partial.start() > 0 else len(window)
    return cut, cut


def split_for_telegram(text: str, max_chars: int = 4096) -> list[str]:
    chunks: list[str] = []
    rest = text
    while (size := _fit(rest, max_chars)) < len(rest):
        if size == 0:
            raise ValueError("max_chars too small to fit the next character")
        end, resume = _cut_point(rest[:size])
        chunks.append(rest[:end])
        rest = rest[resume:].lstrip()
    if rest or not chunks:
        chunks.append(rest)
    return chunks


def format_auth_alert(texts: Texts) -> str:
    return _escape(texts.auth_alert)
