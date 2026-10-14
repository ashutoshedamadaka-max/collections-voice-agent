"""The spoken, spelled-out invoice form ("S, G, P, slash, ...") must resolve back to the raw
identifier — sim-eval found the model passing it as a tool argument (docs/FAILURES.md,
2026-10-14), which silently downgraded a complete promise to a soft commitment."""

from __future__ import annotations

from datetime import date

import pytest

from collections_agent.models.domain import Invoice
from collections_agent.postcall.specialists.promise import _matches_invoice
from collections_agent.voice.speakable import invoice_number_to_words, invoice_words_to_number


@pytest.mark.parametrize("raw", ["KFC/26-27/0026", "SGP/26-27/0014", "USCS/26-27/0002", "KA-3281"])
def test_spoken_form_round_trips(raw):
    assert invoice_words_to_number(invoice_number_to_words(raw)) == raw


@pytest.mark.parametrize(
    "value", ["SGP/26-27/0014", "invoice fourteen", "two lakh rupees", "", "S, G, P, banana"]
)
def test_non_spoken_values_are_left_alone(value):
    assert invoice_words_to_number(value) is None


def test_promise_matcher_accepts_the_spoken_form():
    invoice = Invoice(
        invoice_id="INV-00014",
        account_id="ACC-1",
        invoice_number="SGP/26-27/0014",
        amount=204500,
        issue_date=date(2026, 8, 5),
        due_date=date(2026, 9, 4),
    )
    assert _matches_invoice(invoice, [invoice_number_to_words("SGP/26-27/0014")])
    assert not _matches_invoice(invoice, [invoice_number_to_words("SGP/26-27/0021")])
