import html
import json
import re
from dataclasses import dataclass

from youtube_summary.feed import Video

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_AUTH_ALERT = (
    "⚠️ La sesión de NotebookLM ha caducado. Renueva storage_state.json (ver README). "
    "Los vídeos pendientes se enviarán al renovarla."
)
_SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+")
_GAP = re.compile(r"\s+")
_PARTIAL_ENTITY = re.compile(r"&#?\w*$")
_CITATION = re.compile(r"\s*\[\d+(?:\s*[,-]\s*\d+)*\]")


@dataclass(frozen=True)
class Summary:
    text: str
    key_points: tuple[str, ...]


def parse_answer(answer: str) -> Summary:
    cleaned = _CITATION.sub("", _FENCE.sub("", answer.strip())).strip()
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


def format_message(video: Video, summary: Summary) -> str:
    title = video.title or "Sin título"
    blocks = [f"🎬 <b>{_escape(video.channel_name)}</b> — {_escape(title)}", _escape(summary.text)]
    if summary.key_points:
        blocks.append("\n".join(f"• {_escape(point)}" for point in summary.key_points))
    blocks.append(f'🔗 <a href="{html.escape(video.url, quote=True)}">Ver vídeo</a>')
    return "\n\n".join(blocks)


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
        end, resume = _cut_point(rest[:size])
        chunks.append(rest[:end])
        rest = rest[resume:].lstrip()
    chunks.append(rest)
    return chunks


def format_auth_alert() -> str:
    return _escape(_AUTH_ALERT)
