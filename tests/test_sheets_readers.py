"""Round-trips Account/Invoice through the same fake backend used elsewhere
(tests/conftest.py's InMemorySheetsBackend) via the real writers, then reads them back —
this is exactly the write-then-read path the real Sheet goes through, just without the
network.
"""

from __future__ import annotations

from collections_agent.sheets.readers import read_accounts, read_invoices
from collections_agent.sheets.writers import ensure_all_tabs, seed_accounts, seed_invoices


def test_round_trips_accounts(fake_sheets_backend, base_account):
    ensure_all_tabs(fake_sheets_backend)
    seed_accounts(fake_sheets_backend, [base_account])

    result = read_accounts(fake_sheets_backend)

    assert len(result) == 1
    assert result[0] == base_account


def test_round_trips_invoices(fake_sheets_backend, base_account, make_invoice):
    inv = make_invoice(base_account.account_id, amount=123_456)
    ensure_all_tabs(fake_sheets_backend)
    seed_invoices(fake_sheets_backend, [inv])

    result = read_invoices(fake_sheets_backend)

    assert len(result) == 1
    assert result[0] == inv


def test_handles_blank_bool_cell(fake_sheets_backend, base_account):
    """A manually-cleared checkbox-style cell comes back as "" from gspread — must not crash."""
    ensure_all_tabs(fake_sheets_backend)
    seed_accounts(fake_sheets_backend, [base_account])
    fake_sheets_backend.tabs["Accounts"][0]["opted_out"] = ""

    result = read_accounts(fake_sheets_backend)

    assert result[0].opted_out is False


def test_empty_tab_returns_empty_list(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    assert read_accounts(fake_sheets_backend) == []
    assert read_invoices(fake_sheets_backend) == []
