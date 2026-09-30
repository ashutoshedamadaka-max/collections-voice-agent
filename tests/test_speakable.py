"""Pure unit tests for voice/speakable.py — converts amounts, invoice numbers, and dates into
words before they reach the prompt, so the voice provider never gets raw numerals to mumble
through. See docs/FAILURES.md, 2026-09-16, 2026-09-17, and 2026-09-26/29 entries."""

from __future__ import annotations

from datetime import date

from collections_agent.voice.speakable import amount_to_words, date_to_words, invoice_number_to_words


def test_amount_to_words_matches_sample_context_pack_fixture_value():
    """123_456 is the exact amount tests/conftest.py's sample_context_pack fixture uses."""
    assert amount_to_words(123_456) == "one lakh twenty three thousand four hundred fifty six rupees"


def test_amount_to_words_lakh_and_thousand():
    assert amount_to_words(204_000) == "two lakh four thousand rupees"


def test_amount_to_words_thousand_only():
    assert amount_to_words(46_000) == "forty six thousand rupees"


def test_amount_to_words_zero():
    assert amount_to_words(0) == "zero rupees"


def test_amount_to_words_crore_range():
    assert amount_to_words(12_345_678) == (
        "one crore twenty three lakh forty five thousand six hundred seventy eight rupees"
    )


def test_amount_to_words_rounds_to_nearest_rupee():
    assert amount_to_words(500.6) == "five hundred one rupees"


def test_invoice_number_to_words_spells_real_generator_format():
    assert invoice_number_to_words("KFC/26-27/0026") == (
        "K, F, C, slash, two, six, dash, two, seven, slash, zero, zero, two, six"
    )


def test_invoice_number_to_words_spells_plain_id_format():
    assert invoice_number_to_words("INV-00026") == (
        "I, N, V, dash, zero, zero, zero, two, six"
    )


def test_date_to_words_matches_the_gpt4o_misreading_case():
    """Regression guard for the exact failure that prompted this function: gpt-4o said "2026
    September 13" for 2026-09-30 because dates had no spoken form (docs/FAILURES.md,
    2026-09-26/29)."""
    assert date_to_words(date(2026, 9, 30)) == "thirtieth of September, twenty twenty six"


def test_date_to_words_single_digit_day():
    assert date_to_words(date(2026, 7, 1)) == "first of July, twenty twenty six"


def test_date_to_words_twenty_first_uses_hyphenated_ordinal():
    assert date_to_words(date(2026, 1, 21)) == "twenty-first of January, twenty twenty six"


def test_date_to_words_year_with_single_digit_remainder():
    assert date_to_words(date(2005, 3, 15)) == "fifteenth of March, twenty oh five"


def test_date_to_words_round_century_year():
    assert date_to_words(date(2000, 12, 25)) == "twenty-fifth of December, twenty hundred"
