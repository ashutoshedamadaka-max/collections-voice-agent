from __future__ import annotations

from collections_agent.models.domain import (
    PTP,
    CallLogEntry,
    CallOutcomeType,
    PaymentMethod,
    PTPStatus,
)
from collections_agent.precall.priority import priority_score
from collections_agent.precall.rules_config import load_priority_weights


def _weights():
    return load_priority_weights()


def test_zero_priority_when_no_unpaid_invoices(base_account, as_of):
    score = priority_score(base_account, [], [], [], _weights(), as_of=as_of)
    assert score == 0.0


def test_higher_bucket_yields_higher_priority(base_account, make_invoice, as_of):
    weights = _weights()
    inv_1_30 = make_invoice(base_account.account_id, days_overdue=10)
    inv_61_90 = make_invoice(base_account.account_id, days_overdue=70)

    score_1_30 = priority_score(base_account, [inv_1_30], [], [], weights, as_of=as_of)
    score_61_90 = priority_score(base_account, [inv_61_90], [], [], weights, as_of=as_of)

    assert score_61_90 > score_1_30


def test_higher_balance_yields_higher_priority(base_account, make_invoice, as_of):
    weights = _weights()
    small = make_invoice(base_account.account_id, days_overdue=10, amount=10_000)
    large = make_invoice(base_account.account_id, days_overdue=10, amount=1_000_000)

    score_small = priority_score(base_account, [small], [], [], weights, as_of=as_of)
    score_large = priority_score(base_account, [large], [], [], weights, as_of=as_of)

    assert score_large > score_small


def test_balance_weight_is_capped(base_account, make_invoice, as_of):
    weights = _weights()
    huge = make_invoice(base_account.account_id, days_overdue=10, amount=50_000_000)
    massive = make_invoice(base_account.account_id, days_overdue=10, amount=500_000_000)

    score_huge = priority_score(base_account, [huge], [], [], weights, as_of=as_of)
    score_massive = priority_score(base_account, [massive], [], [], weights, as_of=as_of)

    assert score_huge == score_massive  # both past the cap


def test_broken_promises_lower_priority(base_account, make_invoice, as_of, now):
    weights = _weights()
    inv = make_invoice(base_account.account_id, days_overdue=10)

    kept_ptp = PTP(
        ptp_id="PTP-1",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=as_of,
        payment_method=PaymentMethod.NEFT,
        captured_at=now,
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.KEPT,
        call_id="C1",
    )
    broken_ptp = PTP(
        ptp_id="PTP-2",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=as_of,
        payment_method=PaymentMethod.NEFT,
        captured_at=now,
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.BROKEN,
        call_id="C2",
    )

    score_with_kept = priority_score(base_account, [inv], [kept_ptp], [], weights, as_of=as_of)
    score_with_broken = priority_score(base_account, [inv], [broken_ptp], [], weights, as_of=as_of)

    assert score_with_broken < score_with_kept


def test_wrong_person_lowers_contactability(base_account, make_invoice, as_of, now):
    weights = _weights()
    inv = make_invoice(base_account.account_id, days_overdue=10)

    normal_call = CallLogEntry(
        call_id="C1",
        account_id=base_account.account_id,
        called_at=now,
        duration_seconds=60,
        outcome=CallOutcomeType.PROMISE_TO_PAY,
    )
    wrong_person_call = CallLogEntry(
        call_id="C2",
        account_id=base_account.account_id,
        called_at=now,
        duration_seconds=30,
        outcome=CallOutcomeType.WRONG_PERSON,
    )

    score_normal = priority_score(base_account, [inv], [], [normal_call], weights, as_of=as_of)
    score_wrong_person = priority_score(base_account, [inv], [], [wrong_person_call], weights, as_of=as_of)

    assert score_wrong_person < score_normal


def test_score_ignores_other_accounts_data(base_account, make_invoice, as_of):
    weights = _weights()
    inv = make_invoice(base_account.account_id, days_overdue=10)
    other_inv = make_invoice("ACC-OTHER", days_overdue=95, amount=999_999)

    score = priority_score(base_account, [inv, other_inv], [], [], weights, as_of=as_of)
    solo_score = priority_score(base_account, [inv], [], [], weights, as_of=as_of)

    assert score == solo_score
