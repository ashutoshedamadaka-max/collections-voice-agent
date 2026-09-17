from __future__ import annotations

from collections_agent.data.fake_data_gen import generate_fake_ar_data
from collections_agent.sheets.writers import TAB_SCHEMAS, ensure_all_tabs, seed_accounts, seed_invoices


def test_ensure_all_tabs_creates_every_schema(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    assert set(fake_sheets_backend.tabs.keys()) == set(TAB_SCHEMAS.keys())


def test_seed_accounts_and_invoices_row_counts(fake_sheets_backend):
    accounts, invoices = generate_fake_ar_data()
    ensure_all_tabs(fake_sheets_backend)
    seed_accounts(fake_sheets_backend, accounts)
    seed_invoices(fake_sheets_backend, invoices)

    assert len(fake_sheets_backend.tabs["Accounts"]) == len(accounts)
    assert len(fake_sheets_backend.tabs["Invoices"]) == len(invoices)


def test_reseeding_is_idempotent_no_duplicates(fake_sheets_backend):
    accounts, invoices = generate_fake_ar_data()
    ensure_all_tabs(fake_sheets_backend)

    seed_accounts(fake_sheets_backend, accounts)
    seed_accounts(fake_sheets_backend, accounts)  # re-run
    seed_invoices(fake_sheets_backend, invoices)
    seed_invoices(fake_sheets_backend, invoices)  # re-run

    assert len(fake_sheets_backend.tabs["Accounts"]) == len(accounts)
    assert len(fake_sheets_backend.tabs["Invoices"]) == len(invoices)


def test_reseeding_updates_changed_fields(fake_sheets_backend):
    accounts, invoices = generate_fake_ar_data()
    ensure_all_tabs(fake_sheets_backend)
    seed_accounts(fake_sheets_backend, accounts)

    accounts[0].reliability_score = 0.11
    seed_accounts(fake_sheets_backend, [accounts[0]])

    row = next(r for r in fake_sheets_backend.tabs["Accounts"] if r["account_id"] == accounts[0].account_id)
    assert row["reliability_score"] == "0.11"
