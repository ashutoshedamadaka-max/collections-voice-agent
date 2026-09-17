"""Pure unit tests for voice/language.py — the single place that decides what
account.preferred_language means for the voice provider, the transcriber, and the prompt."""

from __future__ import annotations

from collections_agent.voice.language import prompt_instruction, resolve, transcriber_language, voice_language


def test_resolve_defaults_unset_to_english():
    assert resolve(None) == "en"
    assert resolve("") == "en"


def test_resolve_defaults_unrecognized_value_to_english():
    assert resolve("klingon") == "en"


def test_resolve_passes_through_known_values():
    assert resolve("en") == "en"
    assert resolve("hi") == "hi"
    assert resolve("hinglish") == "hinglish"


def test_voice_language_hindi_maps_to_hindi():
    assert voice_language("hi") == "hi"


def test_voice_language_hinglish_maps_to_english():
    """No voice provider used here has a dedicated Hinglish code — the model writes Hinglish
    in Latin script with English numbers/terms (see prompt_instruction), which an English
    voice can speak correctly."""
    assert voice_language("hinglish") == "en"


def test_voice_language_unset_defaults_to_english():
    assert voice_language(None) == "en"


def test_transcriber_language_hinglish_maps_to_hindi_not_english():
    """Unlike voice, the transcriber gets Hindi for hinglish — a real call showed "en" mis-
    rendering genuinely code-switched Hindi/English audio as Urdu script (docs/FAILURES.md)."""
    assert transcriber_language("hinglish") == "hi"


def test_transcriber_language_hindi_maps_to_hindi():
    assert transcriber_language("hi") == "hi"


def test_transcriber_language_english_maps_to_english():
    assert transcriber_language("en") == "en"


def test_prompt_instruction_differs_per_language():
    en, hi, hinglish = (
        prompt_instruction("en"),
        prompt_instruction("hi"),
        prompt_instruction("hinglish"),
    )
    assert "English" in en
    assert "Hindi" in hi
    assert "Latin" in hinglish
    assert len({en, hi, hinglish}) == 3


def test_hinglish_instruction_specifies_latin_script_and_english_numbers():
    """Regression guard for the 2026-09-17 finding: the original instruction never said which
    script to use, and the model defaulted to formal Devanagari Hindi instead of Hinglish."""
    instruction = prompt_instruction("hinglish")
    assert "Latin" in instruction or "Roman" in instruction
    assert "Devanagari" in instruction  # explicitly named as what NOT to do
    assert "conversational" in instruction.lower() or "informal" in instruction.lower()
