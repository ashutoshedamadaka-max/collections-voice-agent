"""Pure unit tests for voice/language.py — the single place that decides what
account.preferred_language means for the voice provider, the transcriber, and the prompt."""

from __future__ import annotations

from collections_agent.voice.language import prompt_instruction, provider_language, resolve


def test_resolve_defaults_unset_to_english():
    assert resolve(None) == "en"
    assert resolve("") == "en"


def test_resolve_defaults_unrecognized_value_to_english():
    assert resolve("klingon") == "en"


def test_resolve_passes_through_known_values():
    assert resolve("en") == "en"
    assert resolve("hi") == "hi"
    assert resolve("hinglish") == "hinglish"


def test_provider_language_hindi_maps_to_hindi():
    assert provider_language("hi") == "hi"


def test_provider_language_hinglish_maps_to_english():
    """No provider used here has a dedicated Hinglish code — English voice/transcriber,
    code-switching handled by the prompt instruction instead."""
    assert provider_language("hinglish") == "en"


def test_provider_language_unset_defaults_to_english():
    assert provider_language(None) == "en"


def test_prompt_instruction_differs_per_language():
    en, hi, hinglish = (
        prompt_instruction("en"),
        prompt_instruction("hi"),
        prompt_instruction("hinglish"),
    )
    assert "English" in en
    assert "Hindi" in hi
    assert "code-switch" in hinglish.lower()
    assert len({en, hi, hinglish}) == 3
