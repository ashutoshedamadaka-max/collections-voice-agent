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

Voice and transcriber language diverge for hinglish (2026-09-17, after a real test call): the
voice still gets English (no provider used here has a dedicated Hinglish code, and the model
now writes Hinglish in Latin script with English numbers/terms, which an English voice handles
fine), but the transcriber gets Hindi — "en" was given genuinely code-switched Hindi/English
audio on a real call and rendered the customer's speech in Urdu script instead of anything
usable; "hi" handles code-switched speech better. See docs/FAILURES.md for the transcript.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# Vapi voice language, keyed by preferred_language. Hinglish has no dedicated code on the
# `vapi` voice provider — it stays English, since the model writes Hinglish in Latin script
# with English numbers/terms (see _PROMPT_INSTRUCTION below), which an English voice can speak.
_VOICE_LANGUAGE = {"en": "en", "hi": "hi", "hinglish": "en"}

# Soniox transcriber language. Unlike voice, hinglish maps to "hi" here, not "en" — see module
# docstring for why (a real call showed "en" mis-rendering Hindi audio as Urdu script).
_TRANSCRIBER_LANGUAGE = {"en": "en", "hi": "hi", "hinglish": "hi"}

_PROMPT_INSTRUCTION = {
    "en": "Respond in English throughout the call.",
    "hi": "Respond in Hindi throughout the call, matching the customer's own language.",
    "hinglish": (
        "The customer's preferred language is Hinglish: Hindi conversation written in Latin "
        "(Roman) script, not Devanagari — for example 'Payment kab tak ho jayega?', never "
        "'भुगतान कब तक हो जाएगा?'. Keep all numbers, amounts, invoice IDs, dates, and business "
        "or technical terms in English (payment, invoice, approval, NEFT, RTGS, UPI, date) — "
        "translating these into Hindi words is wrong, and the spoken forms you were given for "
        "amounts and invoice numbers are English words that only read naturally inside a "
        "Latin-script sentence. Use a conversational, informal register, the way people "
        "actually speak and text Hinglish — not formal textbook Hindi. Example: 'Invoice "
        "SL/26-27/0002 pending hai, two lakh four thousand. Payment kab tak ho jayega?'"
    ),
}


def resolve(preferred_language: str | None) -> str:
    """Normalizes to one of the keys above, defaulting to English for unset or unrecognized
    values — never fails on an unexpected string."""
    return preferred_language if preferred_language in _VOICE_LANGUAGE else DEFAULT_LANGUAGE


def voice_language(preferred_language: str | None) -> str:
    return _VOICE_LANGUAGE[resolve(preferred_language)]


def transcriber_language(preferred_language: str | None) -> str:
    return _TRANSCRIBER_LANGUAGE[resolve(preferred_language)]


def prompt_instruction(preferred_language: str | None) -> str:
    return _PROMPT_INSTRUCTION[resolve(preferred_language)]
