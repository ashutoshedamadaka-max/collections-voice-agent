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

Supports English and Hindi only — see docs/FAILURES.md, 2026-09-29 for why Hinglish was
dropped: two separately-worded rewrites of its prompt instruction both failed the same way
(the model settled into formal Devanagari regardless), it was the least-tested path through
this whole pipeline, and validating it further would cost real Vapi credits with no confidence
a third rewrite would fare any better. `resolve()` treats "hinglish" (or any other unrecognized
value, including a Sheet row a fake-data run generated before this change) as unset and falls
back to English — not a crash, but also not a silent attempt to keep serving it.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

_SUPPORTED_LANGUAGES = ("en", "hi")

_VOICE_LANGUAGE = {"en": "en", "hi": "hi"}
_TRANSCRIBER_LANGUAGE = {"en": "en", "hi": "hi"}

_PROMPT_INSTRUCTION = {
    "en": "Respond in English throughout the call.",
    "hi": "Respond in Hindi throughout the call, matching the customer's own language.",
}

# The merged opening (2026-09-17): disclosure + the authority question in one firstMessage,
# spoken deterministically by Vapi before the model ever runs — so it can't be reworded,
# skipped, or asked a second time by the model, and so the caller's first turn answers a real
# question instead of a pause after "this call may be recorded." Hand-written per language
# (not run through the model), since nothing here is model-generated — each variant is what
# actually gets spoken, verbatim.
_OPENING_WITH_NAME = {
    "en": (
        "Hello, this is an automated call from {company}'s accounts team about an overdue "
        "invoice. This call may be recorded. Am I speaking with {contact_name}?"
    ),
    "hi": (
        "नमस्ते, यह {company} की अकाउंट्स टीम की ओर से एक स्वचालित कॉल है, एक बकाया इनवॉइस के "
        "संबंध में। इस कॉल को रिकॉर्ड किया जा सकता है। क्या मेरी बात {contact_name} जी से हो रही है?"
    ),
}

# Used only for the assistant's base/fallback firstMessage (no ContextPack, so no contact name
# — e.g. a dashboard-triggered call bypassing assistantOverrides). Real calls always go through
# build_call_overrides and get the personalized version above.
_OPENING_GENERIC = {
    "en": (
        "Hello, this is an automated call from {company}'s accounts team about an overdue "
        "invoice. This call may be recorded. Am I speaking with the person who handles "
        "accounts payable?"
    ),
    "hi": (
        "नमस्ते, यह {company} की अकाउंट्स टीम की ओर से एक स्वचालित कॉल है, एक बकाया इनवॉइस के "
        "संबंध में। इस कॉल को रिकॉर्ड किया जा सकता है। क्या मेरी बात अकाउंट्स पेएबल संभालने वाले "
        "व्यक्ति से हो रही है?"
    ),
}


def resolve(preferred_language: str | None) -> str:
    """Normalizes to one of the supported languages, defaulting to English for unset or
    unrecognized values (including "hinglish" — see module docstring) — never fails on an
    unexpected string."""
    return preferred_language if preferred_language in _SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def voice_language(preferred_language: str | None) -> str:
    return _VOICE_LANGUAGE[resolve(preferred_language)]


def transcriber_language(preferred_language: str | None) -> str:
    return _TRANSCRIBER_LANGUAGE[resolve(preferred_language)]


def prompt_instruction(preferred_language: str | None) -> str:
    return _PROMPT_INSTRUCTION[resolve(preferred_language)]


def opening_message(
    preferred_language: str | None, company_name: str, contact_name: str | None = None
) -> str:
    """The merged disclosure + authority-question firstMessage. `contact_name` is omitted only
    for the assistant's base/fallback config, which has no per-call ContextPack to draw one
    from."""
    lang = resolve(preferred_language)
    if contact_name:
        return _OPENING_WITH_NAME[lang].format(company=company_name, contact_name=contact_name)
    return _OPENING_GENERIC[lang].format(company=company_name)
