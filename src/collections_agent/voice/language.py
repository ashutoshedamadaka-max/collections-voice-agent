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


# The merged opening (2026-09-17): disclosure + the authority question in one firstMessage,
# spoken deterministically by Vapi before the model ever runs — so it can't be reworded,
# skipped, or asked a second time by the model, and so the caller's first turn answers a real
# question instead of a pause after "this call may be recorded." Hand-written per language
# (not run through the model) for the same reason `_PROMPT_INSTRUCTION` above is hand-written —
# nothing here is model-generated, so there's no "code-switch naturally" instruction that could
# apply to it; each variant is what actually gets spoken, verbatim.
_OPENING_WITH_NAME = {
    "en": (
        "Hello, this is an automated call from {company}'s accounts team about an overdue "
        "invoice. This call may be recorded. Am I speaking with {contact_name}?"
    ),
    "hi": (
        "नमस्ते, यह {company} की अकाउंट्स टीम की ओर से एक स्वचालित कॉल है, एक बकाया इनवॉइस के "
        "संबंध में। इस कॉल को रिकॉर्ड किया जा सकता है। क्या मेरी बात {contact_name} जी से हो रही है?"
    ),
    "hinglish": (
        "Hello, this is an automated call from {company}'s accounts team, ek overdue invoice "
        "ke baare mein. Yeh call record ho sakti hai. Kya main {contact_name} se baat kar raha "
        "hoon?"
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
    "hinglish": (
        "Hello, this is an automated call from {company}'s accounts team, ek overdue invoice "
        "ke baare mein. Yeh call record ho sakti hai. Kya main accounts payable dekhne wale se "
        "baat kar raha hoon?"
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
