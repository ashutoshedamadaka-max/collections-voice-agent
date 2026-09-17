"""Converts amounts and invoice numbers into words before they ever reach the prompt, so the
TTS provider is never handed raw numerals or an alphanumeric ID to mumble through.

See docs/FAILURES.md (2026-09-16, "numbers are the agent's weakest speech moment," and
2026-09-17's two prompt-defect entries). Neither SSML nor a pronunciation dictionary is
available for the voice currently configured (provider "vapi", voiceId "Naina") — both are
ElevenLabs/Cartesia/WellSaid-only per Vapi's current OpenAPI spec — so pre-formatting the text
itself is the fix, not a stand-in for a better one.

Callers keep the raw value alongside whatever this module produces (see prompt_template.py) —
the model still needs the literal invoice number/amount for structured tool-call arguments;
only what gets *spoken* should come from here.
"""

from __future__ import annotations

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

_LETTER_WORDS = {
    "/": "slash",
    "-": "dash",
}


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return f"{_TENS[tens]} {_ONES[ones]}" if ones else _TENS[tens]


def _three_digit_words(n: int) -> str:
    if n < 100:
        return _two_digit_words(n)
    hundreds, rest = divmod(n, 100)
    if rest:
        return f"{_ONES[hundreds]} hundred {_two_digit_words(rest)}"
    return f"{_ONES[hundreds]} hundred"


def _number_to_indian_words(n: int) -> str:
    """Indian numbering: crore (10,000,000) / lakh (100,000) / thousand / hundred."""
    if n == 0:
        return "zero"
    parts: list[str] = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, hundred_rest = divmod(n, 1000)
    if crore:
        parts.append(f"{_three_digit_words(crore)} crore")
    if lakh:
        parts.append(f"{_three_digit_words(lakh)} lakh")
    if thousand:
        parts.append(f"{_three_digit_words(thousand)} thousand")
    if hundred_rest:
        parts.append(_three_digit_words(hundred_rest))
    return " ".join(parts)


def amount_to_words(amount: float) -> str:
    """e.g. 204000.0 -> "two lakh four thousand rupees". Rounds to the nearest whole rupee —
    every amount this project generates or promises is a whole number; paise are not a
    realistic case for a spoken B2B collections call."""
    return f"{_number_to_indian_words(round(amount))} rupees"


def invoice_number_to_words(value: str) -> str:
    """Spells every character: letters by name, digits by name, '/' -> "slash", '-' -> "dash".
    Generalizes the project's letter-by-letter convention (originally written against toy IDs
    like "KA-3281") to the real generator's "KFC/26-27/0026"-style format."""
    words = []
    for ch in value:
        if ch.isalpha():
            words.append(ch.upper())
        elif ch.isdigit():
            words.append(_ONES[int(ch)])
        elif ch in _LETTER_WORDS:
            words.append(_LETTER_WORDS[ch])
        elif not ch.isspace():
            words.append(ch)
    return ", ".join(words)
