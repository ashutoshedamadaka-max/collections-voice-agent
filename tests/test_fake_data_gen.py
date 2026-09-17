from __future__ import annotations

from datetime import date

from collections_agent.data.fake_data_gen import generate_fake_ar_data
from collections_agent.models.domain import AgingBucket


def test_generates_expected_counts():
    accounts, invoices = generate_fake_ar_data()
    assert len(accounts) == 25
    assert len(invoices) == 60


def test_every_invoice_references_a_real_account():
    accounts, invoices = generate_fake_ar_data()
    account_ids = {a.account_id for a in accounts}
    assert all(inv.account_id in account_ids for inv in invoices)


def test_covers_every_aging_bucket():
    _, invoices = generate_fake_ar_data()
    today = date.today()
    buckets = {inv.aging_bucket(today) for inv in invoices}
    assert buckets == set(AgingBucket)


def test_reproducible_with_same_seed():
    accounts_a, invoices_a = generate_fake_ar_data(seed=7)
    accounts_b, invoices_b = generate_fake_ar_data(seed=7)
    assert [a.account_id for a in accounts_a] == [a.account_id for a in accounts_b]
    assert [a.customer_name for a in accounts_a] == [a.customer_name for a in accounts_b]
    assert [i.amount for i in invoices_a] == [i.amount for i in invoices_b]


def test_some_accounts_are_opted_out_or_wrong_party_for_suppression_tests():
    accounts, _ = generate_fake_ar_data()
    assert any(a.opted_out for a in accounts)
    assert any(a.wrong_party for a in accounts)
    assert any(a.in_active_payment_plan for a in accounts)
