from datetime import UTC, datetime

from youtube_summary.feed import Video
from youtube_summary.message import (
    Summary,
    format_auth_alert,
    format_message,
    parse_answer,
    split_for_telegram,
)


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def make_video(title: str = "Título", url: str = "https://youtu.be/x") -> Video:
    return Video("x", url, title, datetime(2026, 1, 1, tzinfo=UTC), "Canal")


def test_parse_answer_reads_json() -> None:
    answer = '{"summary": " Sube. ", "key_points": [" A ", "", 3, "B"]}'
    assert parse_answer(answer) == Summary("Sube.", ("A", "B"))


def test_parse_answer_strips_fences_and_citations() -> None:
    answer = '```json\n{"summary": "Sube [1].", "key_points": ["A [2, 3]"]}\n```'
    assert parse_answer(answer) == Summary("Sube.", ("A",))


def test_parse_answer_falls_back_to_raw_text() -> None:
    assert parse_answer("Texto libre [1]") == Summary("Texto libre", ())


def test_parse_answer_tolerates_missing_or_invalid_key_points() -> None:
    assert parse_answer('{"summary": "S"}') == Summary("S", ())
    assert parse_answer('{"summary": "S", "key_points": "x"}') == Summary("S", ())


def test_parse_answer_json_without_summary_falls_back() -> None:
    assert parse_answer('{"summary": ""}') == Summary('{"summary": ""}', ())
    assert parse_answer('["x"]') == Summary('["x"]', ())


def test_format_message_escapes_html_specials() -> None:
    video = make_video('S&P 500 <live> "2025"', 'https://youtu.be/x?a=1&b="2"')
    message = format_message(video, Summary("a < b & c", ("p > q",)))
    assert '<b>Canal</b> — S&amp;P 500 &lt;live&gt; "2025"' in message
    assert "a &lt; b &amp; c" in message
    assert "• p &gt; q" in message
    assert '<a href="https://youtu.be/x?a=1&amp;b=&quot;2&quot;">Ver vídeo</a>' in message


def test_format_message_omits_empty_key_points() -> None:
    assert "•" not in format_message(make_video(), Summary("Texto", ()))


def test_format_message_layout_and_untitled() -> None:
    message = format_message(make_video(title=""), Summary("Texto", ("A", "B")))
    assert message == (
        "🎬 <b>Canal</b> — Sin título\n\nTexto\n\n• A\n• B\n\n"
        '🔗 <a href="https://youtu.be/x">Ver vídeo</a>'
    )


def test_format_auth_alert_is_spanish_text() -> None:
    assert format_auth_alert() == (
        "⚠️ La sesión de NotebookLM ha caducado. Renueva storage_state.json (ver README). "
        "Los vídeos pendientes se enviarán al renovarla."
    )


def test_split_for_telegram_returns_short_text_unchanged() -> None:
    assert split_for_telegram("hola") == ["hola"]


def test_split_for_telegram_hard_cuts_oversized_sentence() -> None:
    text = "a" * 9000
    chunks = split_for_telegram(text)
    assert len(chunks) == 3
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == text


def test_split_for_telegram_never_cuts_inside_entity() -> None:
    chunks = split_for_telegram("a" * 4094 + "&amp;" + "b" * 10)
    assert all(utf16_len(chunk) <= 4096 for chunk in chunks)
    assert chunks[0] == "a" * 4094
    assert chunks[1] == "&amp;" + "b" * 10


def test_split_for_telegram_splits_on_sentences() -> None:
    chunks = split_for_telegram("Uno dos. Tres cuatro! Cinco seis?", max_chars=22)
    assert chunks == ["Uno dos. Tres cuatro!", "Cinco seis?"]


def test_split_for_telegram_cuts_at_whitespace_without_sentences() -> None:
    assert split_for_telegram("aaaa bbbb cccc", max_chars=10) == ["aaaa bbbb", "cccc"]


def test_split_for_telegram_counts_utf16_units() -> None:
    chunks = split_for_telegram("😀" * 3000)
    assert all(utf16_len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == "😀" * 3000


def test_split_for_telegram_keeps_paragraph_breaks_inside_chunks() -> None:
    paragraph = "Frase uno. Frase dos. " * 40
    text = "\n\n".join([paragraph.strip()] * 8)
    chunks = split_for_telegram(text)
    assert len(chunks) > 1
    assert all(utf16_len(chunk) <= 4096 for chunk in chunks)
    assert "\n\n" in chunks[0]
