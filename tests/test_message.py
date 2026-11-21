import re
from datetime import UTC, datetime

import pytest

from youtube_summary.feed import Video
from youtube_summary.message import (
    EmptyAnswerError,
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
    (message,) = format_message(video, Summary("a < b & c", ("p > q",)))
    assert '<b>Canal</b> — S&amp;P 500 &lt;live&gt; "2025"' in message
    assert "a &lt; b &amp; c" in message
    assert "• p &gt; q" in message
    assert '<a href="https://youtu.be/x?a=1&amp;b=&quot;2&quot;">Ver vídeo</a>' in message


def test_format_message_omits_empty_key_points() -> None:
    assert "•" not in format_message(make_video(), Summary("Texto", ()))[0]


def test_format_message_layout_and_untitled() -> None:
    chunks = format_message(make_video(title=""), Summary("Texto", ("A", "B")))
    assert chunks == [
        "🎬 <b>Canal</b> — Sin título\n\nTexto\n\n• A\n• B\n\n"
        '🔗 <a href="https://youtu.be/x">Ver vídeo</a>'
    ]


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


FOOTER = '🔗 <a href="https://youtu.be/x">Ver vídeo</a>'


def test_format_message_long_body_keeps_header_and_footer_intact() -> None:
    chunks = format_message(make_video(), Summary("palabra " * 2000, ("A &",)))
    assert len(chunks) > 1
    assert all(utf16_len(chunk) <= 4096 for chunk in chunks)
    assert chunks[0].startswith("🎬 <b>Canal</b> — Título\n\n")
    assert chunks[-1].endswith(FOOTER)
    for chunk in chunks:
        assert re.sub(r"<a href=[^>]*>|</a>|<b>|</b>", "", chunk).count("<") == 0
        assert chunk.count("<a ") == chunk.count("</a>")


def test_format_message_question_mark_title_does_not_become_cut_point() -> None:
    chunks = format_message(make_video(title="¿Qué pasa?"), Summary("palabra " * 2000, ()))
    assert "palabra" in chunks[0]


def test_split_for_telegram_never_returns_empty_chunk() -> None:
    assert split_for_telegram("a" * 4096 + " ") == ["a" * 4096]


def test_split_for_telegram_raises_when_no_progress_possible() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        split_for_telegram("😀😀", max_chars=1)


def test_parse_answer_handles_any_fence_language_and_lead_in() -> None:
    expected = Summary("S", ("A",))
    body = '{"summary": "S", "key_points": ["A"]}'
    assert parse_answer(f"```JSON\n{body}\n```") == expected
    assert parse_answer(f"```javascript\n{body}\n```") == expected
    assert parse_answer(f"Aquí tienes el resultado:\n```json\n{body}\n```\nFin") == expected


def test_parse_answer_rejects_empty_answer() -> None:
    for answer in ("", "  \n", "[1]", "```json\n```"):
        with pytest.raises(EmptyAnswerError):
            parse_answer(answer)


def test_parse_answer_citation_markers_only_short_numbers() -> None:
    assert parse_answer("Dato [3\u20135] y [1-2] y [7].") == Summary("Dato y y.", ())
    assert parse_answer("En [2024] subió") == Summary("En [2024] subió", ())


def test_format_message_reserves_header_and_footer_room_in_every_chunk() -> None:
    video = make_video()
    (probe,) = format_message(video, Summary("x", ()), max_chars=200)
    header_units = utf16_len(probe.split("\n\n")[0])
    footer_units = utf16_len(FOOTER)
    body_units = 200 - max(header_units, footer_units) - 2
    chunks = format_message(video, Summary("x" * body_units, ()), max_chars=200)
    assert all(utf16_len(chunk) <= 200 for chunk in chunks)
