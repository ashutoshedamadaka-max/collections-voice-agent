"""Converts amounts, invoice numbers, and dates into words before they ever reach the prompt,
so the TTS provider is never handed raw numerals or an alphanumeric ID to mumble through.

See docs/FAILURES.md (2026-09-16, "numbers are the agent's weakest speech moment," 2026-09-17's
two prompt-defect entries, and 2026-09-26/29's date-reading finding — gpt-4o said "2026
September 13" for 2026-09-30 because dates had no spoken form at all, the only one of the three
fact types that didn't). Neither SSML nor a pronunciation dictionary is available for the voice
currently configured (provider "vapi", voiceId "Naina") — both are ElevenLabs/Cartesia/
WellSaid-only per Vapi's current OpenAPI spec — so pre-formatting the text itself is the fix,
not a stand-in for a better one.

As of 2026-09-29 this is the *only* form of a known amount/invoice number/date the prompt ever
contains — see prompt_template.py's docstring for why giving the model both a spoken and a raw
form let it default back to the raw one, and the separate `# REFERENCE VALUES` section that
still carries the handful of raw values (invoice numbers, and an invoice's full outstanding
amount) actually needed as tool-call arguments.
"""

from __future__ import annotations

from datetime import date

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

_ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
    8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth",
    14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth", 21: "twenty-first", 22: "twenty-second",
    23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth", 26: "twenty-sixth",
    27: "twenty-seventh", 28: "twenty-eighth", 29: "twenty-ninth", 30: "thirtieth",
    31: "thirty-first",
}

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


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


def _year_to_words(year: int) -> str:
    century, remainder = divmod(year, 100)
    if remainder == 0:
        return f"{_two_digit_words(century)} hundred"
    if remainder < 10:
        return f"{_two_digit_words(century)} oh {_ONES[remainder]}"
    return f"{_two_digit_words(century)} {_two_digit_words(remainder)}"


def date_to_words(value: date) -> str:
    """e.g. date(2026, 9, 30) -> "thirtieth of September, twenty twenty six". Every date this
    project renders is known well in advance (an invoice's due date, a prior promise's date) —
    the one date that ISN'T known in advance, whatever the caller agrees to mid-call, has no
    pre-computed form here (it can't: the value doesn't exist until the call happens) and
    relies on the CONVERSATION RULE that points back at this same wording convention instead."""
    return f"{_ORDINAL_WORDS[value.day]} of {_MONTH_NAMES[value.month - 1]}, {_year_to_words(value.year)}"


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
