from __future__ import annotations

from datetime import UTC, datetime, timedelta

from collections_agent.models.domain import (
    PTP,
    CallLogEntry,
    CallOutcomeType,
    InvoiceStatus,
    PaymentMethod,
    PTPStatus,
)
from collections_agent.precall.suppression import is_suppressed


def test_not_suppressed_for_clean_unpaid_invoice(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is False
    assert result.reasons == []


def test_suppressed_when_fully_paid(base_account, make_invoice, now):
    inv = make_invoice(
        base_account.account_id, days_overdue=10, status=InvoiceStatus.PAID, amount_paid=100_000
    )
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is True
    assert any("paid" in r for r in result.reasons)


def test_suppressed_when_open_dispute_free_invoice_has_open_future_ptp(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    ptp = PTP(
        ptp_id="PTP-1",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=now.date() + timedelta(days=3),
        payment_method=PaymentMethod.NEFT,
        captured_at=now,
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.OPEN,
        call_id="CALL-1",
    )
    result = is_suppressed(base_account, [inv], [], [ptp], now)
    assert result.suppressed is True
    assert any("promise" in r for r in result.reasons)


def test_not_suppressed_when_ptp_date_has_passed(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    ptp = PTP(
        ptp_id="PTP-1",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=now.date() - timedelta(days=1),
        payment_method=PaymentMethod.NEFT,
        captured_at=now,
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.OPEN,
        call_id="CALL-1",
    )
    result = is_suppressed(base_account, [inv], [], [ptp], now)
    assert result.suppressed is False


def test_suppressed_when_opted_out(base_account, make_invoice, now):
    base_account.opted_out = True
    inv = make_invoice(base_account.account_id, days_overdue=10)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is True
    assert "contact opted out" in result.reasons


def test_suppressed_when_wrong_party(base_account, make_invoice, now):
    base_account.wrong_party = True
    inv = make_invoice(base_account.account_id, days_overdue=10)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is True
    assert "contact marked wrong-party" in result.reasons


def test_suppressed_within_cooldown_window(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    recent_call = CallLogEntry(
        call_id="CALL-1",
        account_id=base_account.account_id,
        called_at=now - timedelta(days=1),
        duration_seconds=60,
        outcome=CallOutcomeType.SOFT_COMMITMENT,
    )
    result = is_suppressed(base_account, [inv], [recent_call], [], now)
    assert result.suppressed is True
    assert any("cooldown" not in r and "working days" in r for r in result.reasons)


def test_not_suppressed_after_cooldown_elapses(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    old_call = CallLogEntry(
        call_id="CALL-1",
        account_id=base_account.account_id,
        called_at=now - timedelta(days=14),
        duration_seconds=60,
        outcome=CallOutcomeType.SOFT_COMMITMENT,
    )
    result = is_suppressed(base_account, [inv], [old_call], [], now)
    assert result.suppressed is False


def test_suppressed_outside_calling_hours(base_account, make_invoice):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    late_night = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)  # ~2:30am IST
    result = is_suppressed(base_account, [inv], [], [], late_night)
    assert result.suppressed is True
    assert any("calling hours" in r for r in result.reasons)


def test_suppressed_on_weekend(base_account, make_invoice):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    saturday = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)  # Saturday late morning IST
    result = is_suppressed(base_account, [inv], [], [], saturday)
    assert result.suppressed is True


def test_suppressed_when_bucket_is_90_plus(base_account, make_invoice, now):
    inv = make_invoice(base_account.account_id, days_overdue=95)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is True
    assert any("90+" in r for r in result.reasons)


def test_suppressed_when_on_schedule_payment_plan(base_account, make_invoice, now):
    base_account.in_active_payment_plan = True
    base_account.payment_plan_on_schedule = True
    inv = make_invoice(base_account.account_id, days_overdue=10)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert result.suppressed is True
    assert any("payment plan" in r for r in result.reasons)


def test_multiple_reasons_all_reported(base_account, make_invoice, now):
    base_account.opted_out = True
    base_account.wrong_party = True
    inv = make_invoice(base_account.account_id, days_overdue=10)
    result = is_suppressed(base_account, [inv], [], [], now)
    assert len(result.reasons) >= 2
