"""Resolves `Account.preferred_language` (via ContextPack) into what the voice provider, the
transcriber, and the prompt each need to do about it.

One shared place so `assistant_config.py` (voice/transcriber config) and `prompt_template.py`
(the prompt instruction) can't drift apart on what a given preferred_language value means.
Verified against Vapi's current OpenAPI spec (not docs prose) before wiring this in: the `vapi`
voice provider's `version` field accepts the literal string `"latest"`, and both its `language`
field and Soniox's `language` field accept `"hi"`.

Language is a pre-call property of the account, decided before the call starts — never asked
of the caller mid-call. See docs/FAILURES.md for why, and for the caveat that everything
downstream of the call (the four post-call specialists, the reason-code taxonomy, arithmetic
extraction) assumes an English transcript and is untested on a Hindi one.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# Language code handed to the voice and transcriber providers, keyed by preferred_language.
# Hinglish has no dedicated code on either provider used here (Vapi voice, Soniox) — it gets
# English voice/transcriber settings, with the prompt instruction below doing the actual
# code-switching rather than the audio pipeline.
_PROVIDER_LANGUAGE = {"en": "en", "hi": "hi", "hinglish": "en"}

_PROMPT_INSTRUCTION = {
    "en": "Respond in English throughout the call.",
    "hi": "Respond in Hindi throughout the call, matching the customer's own language.",
    "hinglish": (
        "The customer's preferred language is Hinglish. Code-switch naturally between Hindi "
        "and English within sentences, the way a bilingual Indian speaker would, rather than "
        "committing to only one language. Match however the caller themselves mixes the two."
    ),
}


def resolve(preferred_language: str | None) -> str:
    """Normalizes to one of the keys above, defaulting to English for unset or unrecognized
    values — never fails on an unexpected string."""
    return preferred_language if preferred_language in _PROVIDER_LANGUAGE else DEFAULT_LANGUAGE


def provider_language(preferred_language: str | None) -> str:
    return _PROVIDER_LANGUAGE[resolve(preferred_language)]


def prompt_instruction(preferred_language: str | None) -> str:
    return _PROMPT_INSTRUCTION[resolve(preferred_language)]
