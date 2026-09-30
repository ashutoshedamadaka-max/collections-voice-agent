"""Pure unit tests for voice/language.py — the single place that decides what
account.preferred_language means for the voice provider, the transcriber, and the prompt."""

from __future__ import annotations

from collections_agent.voice.language import (
    opening_message,
    prompt_instruction,
    resolve,
    transcriber_language,
    voice_language,
)


def test_resolve_defaults_unset_to_english():
    assert resolve(None) == "en"
    assert resolve("") == "en"


def test_resolve_defaults_unrecognized_value_to_english():
    assert resolve("klingon") == "en"


def test_resolve_defaults_hinglish_to_english():
    """Hinglish was dropped 2026-09-29 (see docs/FAILURES.md) — accounts in the Sheet still
    marked "hinglish" from before that change must fall back to English, not crash or silently
    keep getting Hinglish-shaped output from code that no longer exists."""
    assert resolve("hinglish") == "en"


def test_resolve_passes_through_known_values():
    assert resolve("en") == "en"
    assert resolve("hi") == "hi"


def test_voice_language_hindi_maps_to_hindi():
    assert voice_language("hi") == "hi"


def test_voice_language_unset_defaults_to_english():
    assert voice_language(None) == "en"


def test_transcriber_language_hindi_maps_to_hindi():
    assert transcriber_language("hi") == "hi"


def test_transcriber_language_english_maps_to_english():
    assert transcriber_language("en") == "en"


def test_prompt_instruction_differs_per_language():
    en, hi = prompt_instruction("en"), prompt_instruction("hi")
    assert "English" in en
    assert "Hindi" in hi
    assert en != hi


class TestOpeningMessage:
    """2026-09-17: the disclosure and the authority question merged into one deterministic
    firstMessage — hand-written per language, not model-generated, since Vapi speaks this
    verbatim before the model ever runs."""

    def test_generic_english_has_no_name_and_asks_a_generic_authority_question(self):
        message = opening_message(None, "Acme Supplies")
        assert "Acme Supplies" in message
        assert "recorded" in message.lower()
        assert "accounts payable" in message.lower()

    def test_personalized_english_includes_the_contact_name(self):
        message = opening_message("en", "Acme Supplies", "Priya Sharma")
        assert "Priya Sharma" in message
        assert "Acme Supplies" in message
        assert "recorded" in message.lower()

    def test_hindi_opening_uses_devanagari(self):
        message = opening_message("hi", "Acme Supplies", "Priya Sharma")
        assert "Priya Sharma" in message
        assert any(0x900 <= ord(ch) <= 0x97F for ch in message)  # Devanagari block

    def test_unset_language_defaults_to_english(self):
        assert opening_message(None, "Acme Supplies", "Priya Sharma") == opening_message(
            "en", "Acme Supplies", "Priya Sharma"
        )

    def test_no_contact_name_uses_generic_phrasing_in_every_language(self):
        for lang in ("en", "hi"):
            message = opening_message(lang, "Acme Supplies")
            assert "Acme Supplies" in message
            assert message == opening_message(lang, "Acme Supplies", None)
