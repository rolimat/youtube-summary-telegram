from dataclasses import fields

from youtube_summary.texts import DEFAULT_LANGUAGE, TEXTS, Texts


def test_texts_keys_are_english_and_spanish() -> None:
    assert set(TEXTS) == {"en", "es"}
    assert DEFAULT_LANGUAGE in TEXTS


def test_every_language_defines_every_field() -> None:
    for texts in TEXTS.values():
        assert all(getattr(texts, field.name).strip() for field in fields(Texts))
