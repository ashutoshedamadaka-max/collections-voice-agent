"""Tests for check_ptp (pure) and run_followthrough (against InMemorySheetsBackend, no real
Sheets network calls)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from collections_agent.followthrough.job import (
    DEFAULT_GRACE_PERIOD_DAYS,
    check_ptp,
    run_followthrough,
)
from collections_agent.models.domain import PTP, Payment, PTPStatus
from collections_agent.sheets.readers import read_accounts, read_ptps
from collections_agent.sheets.writers import ensure_all_tabs, seed_accounts, write_ptps

PROMISED_DATE = date(2026, 9, 1)
CAPTURED_AT = datetime(2026, 8, 1, tzinfo=UTC)
PAST_GRACE = PROMISED_DATE + timedelta(days=DEFAULT_GRACE_PERIOD_DAYS + 1)
WITHIN_GRACE = PROMISED_DATE + timedelta(days=1)


def _ptp(**overrides) -> PTP:
    fields = dict(
        ptp_id="PTP-1",
        account_id="ACC-0001",
        invoice_ids=["INV-1"],
        amount_promised=50_000,
        promised_date=PROMISED_DATE,
        payment_method="NEFT",
        captured_at=CAPTURED_AT,
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.OPEN,
        call_id="C1",
    )
    fields.update(overrides)
    return PTP(**fields)


def _payment(**overrides) -> Payment:
    fields = dict(invoice_id="INV-1", amount_paid=50_000, paid_date=PROMISED_DATE, method="NEFT")
    fields.update(overrides)
    return Payment(**fields)


class TestCheckPtp:
    def test_full_payment_before_grace_ends_is_kept(self):
        result = check_ptp(_ptp(), [_payment()], as_of=PAST_GRACE)
        assert result.status == PTPStatus.KEPT

    def test_partial_payment_is_partial(self):
        result = check_ptp(_ptp(), [_payment(amount_paid=20_000)], as_of=PAST_GRACE)
        assert result.status == PTPStatus.PARTIAL

    def test_no_payment_past_grace_is_broken(self):
        result = check_ptp(_ptp(), [], as_of=PAST_GRACE)
        assert result.status == PTPStatus.BROKEN

    def test_within_grace_period_stays_open_even_with_no_payment(self):
        result = check_ptp(_ptp(), [], as_of=WITHIN_GRACE)
        assert result.status == PTPStatus.OPEN

    def test_within_grace_period_stays_open_even_if_already_paid_in_full(self):
        """One code path decides the outcome — paying early doesn't resolve it early."""
        result = check_ptp(_ptp(), [_payment()], as_of=WITHIN_GRACE)
        assert result.status == PTPStatus.OPEN

    def test_payments_summed_across_multiple_invoices(self):
        ptp = _ptp(invoice_ids=["INV-1", "INV-2"], amount_promised=50_000)
        payments = [
            _payment(invoice_id="INV-1", amount_paid=30_000),
            _payment(invoice_id="INV-2", amount_paid=20_000),
        ]
        result = check_ptp(ptp, payments, as_of=PAST_GRACE)
        assert result.status == PTPStatus.KEPT

    def test_payment_against_unrelated_invoice_not_counted(self):
        result = check_ptp(_ptp(), [_payment(invoice_id="INV-OTHER")], as_of=PAST_GRACE)
        assert result.status == PTPStatus.BROKEN

    def test_payment_after_check_date_not_counted(self):
        late_payment = _payment(paid_date=PAST_GRACE + timedelta(days=30))
        result = check_ptp(_ptp(), [late_payment], as_of=PAST_GRACE)
        assert result.status == PTPStatus.BROKEN

    @pytest.mark.parametrize(
        "status", [PTPStatus.KEPT, PTPStatus.PARTIAL, PTPStatus.BROKEN, PTPStatus.SUPERSEDED]
    )
    def test_already_resolved_or_superseded_ptp_is_never_touched(self, status):
        ptp = _ptp(status=status)
        result = check_ptp(ptp, [_payment()], as_of=PAST_GRACE)
        assert result.status == status
        assert result == ptp

    def test_rounding_tolerance_treats_paisa_shortfall_as_kept(self):
        result = check_ptp(
            _ptp(amount_promised=50_000), [_payment(amount_paid=49_999.995)], as_of=PAST_GRACE
        )
        assert result.status == PTPStatus.KEPT


class _FakePaymentsSource:
    def __init__(self, payments):
        self._payments = payments

    def list_payments(self):
        return self._payments


class TestRunFollowthrough:
    def test_only_open_ptps_are_touched(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        write_ptps(fake_sheets_backend, [_ptp(ptp_id="PTP-kept", status=PTPStatus.KEPT)])

        result = run_followthrough(fake_sheets_backend, _FakePaymentsSource([]), as_of=PAST_GRACE)

        assert result.resolved == []
        assert read_ptps(fake_sheets_backend)[0].status == PTPStatus.KEPT

    def test_resolves_open_ptp_and_writes_it_back(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        write_ptps(fake_sheets_backend, [_ptp()])

        result = run_followthrough(
            fake_sheets_backend, _FakePaymentsSource([_payment()]), as_of=PAST_GRACE
        )

        assert len(result.resolved) == 1
        assert result.resolved[0].status == PTPStatus.KEPT
        assert read_ptps(fake_sheets_backend)[0].status == PTPStatus.KEPT

    def test_updates_account_reliability_score(self, fake_sheets_backend, base_account):
        ensure_all_tabs(fake_sheets_backend)
        seed_accounts(fake_sheets_backend, [base_account])
        write_ptps(
            fake_sheets_backend,
            [
                _ptp(ptp_id="PTP-1", status=PTPStatus.KEPT, account_id=base_account.account_id),
                _ptp(ptp_id="PTP-2", account_id=base_account.account_id),  # OPEN, resolves BROKEN
            ],
        )

        result = run_followthrough(fake_sheets_backend, _FakePaymentsSource([]), as_of=PAST_GRACE)

        assert result.accounts_updated == [base_account.account_id]
        updated = next(
            a for a in read_accounts(fake_sheets_backend) if a.account_id == base_account.account_id
        )
        assert updated.reliability_score == 0.5  # 1 kept out of 2 resolved

    def test_rerunning_same_day_is_a_no_op(self, fake_sheets_backend, base_account):
        ensure_all_tabs(fake_sheets_backend)
        seed_accounts(fake_sheets_backend, [base_account])
        write_ptps(fake_sheets_backend, [_ptp(account_id=base_account.account_id)])
        payments_source = _FakePaymentsSource([_payment()])

        first = run_followthrough(fake_sheets_backend, payments_source, as_of=PAST_GRACE)
        second = run_followthrough(fake_sheets_backend, payments_source, as_of=PAST_GRACE)

        assert len(first.resolved) == 1
        assert second.resolved == []  # already KEPT, no longer OPEN, nothing to re-touch
        assert len(read_ptps(fake_sheets_backend)) == 1
