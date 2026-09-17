from __future__ import annotations

import pytest

from collections_agent.models.domain import (
    PTP,
    AgingBucket,
    CallLogEntry,
    CallOutcomeType,
    Dispute,
    DisputeStatus,
    PaymentMethod,
    PTPStatus,
    ReasonCode,
    RoutingTarget,
)
from collections_agent.precall.context_pack import build_context_pack


def test_raises_when_no_unpaid_invoices(base_account, as_of):
    with pytest.raises(ValueError):
        build_context_pack(base_account, [], [], [], [], as_of=as_of)


def test_only_includes_unpaid_invoices(base_account, make_invoice, as_of):
    from collections_agent.models.domain import InvoiceStatus

    unpaid = make_invoice(base_account.account_id, days_overdue=10)
    paid = make_invoice(
        base_account.account_id, days_overdue=10, status=InvoiceStatus.PAID, amount_paid=100_000
    )

    pack = build_context_pack(base_account, [unpaid, paid], [], [], [], as_of=as_of)

    assert [i.invoice_id for i in pack.invoices] == [unpaid.invoice_id]
    assert pack.total_outstanding == unpaid.outstanding()


def test_bucket_reflects_most_overdue_invoice(base_account, make_invoice, as_of):
    inv_recent = make_invoice(base_account.account_id, days_overdue=5)
    inv_old = make_invoice(base_account.account_id, days_overdue=70)

    pack = build_context_pack(base_account, [inv_recent, inv_old], [], [], [], as_of=as_of)

    assert pack.bucket == AgingBucket.D61_90


def test_excludes_resolved_disputes(base_account, make_invoice, as_of, now):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    open_dispute = Dispute(
        dispute_id="D1",
        account_id=base_account.account_id,
        invoice_id=inv.invoice_id,
        reason_code=ReasonCode.QUANTITY_DISPUTE,
        detail="short by 10 units",
        routing_target=RoutingTarget.LOGISTICS,
        status=DisputeStatus.OPEN,
        opened_at=now,
        call_id="C1",
    )
    resolved_dispute = Dispute(
        dispute_id="D2",
        account_id=base_account.account_id,
        invoice_id=inv.invoice_id,
        reason_code=ReasonCode.PRICING_DISPUTE,
        detail="rate mismatch",
        routing_target=RoutingTarget.SALES,
        status=DisputeStatus.RESOLVED,
        opened_at=now,
        call_id="C2",
    )

    pack = build_context_pack(base_account, [inv], [], [open_dispute, resolved_dispute], [], as_of=as_of)

    assert [d.dispute_id for d in pack.open_disputes] == ["D1"]


def test_last_contact_is_most_recent_call(base_account, make_invoice, as_of, now):
    from datetime import timedelta

    inv = make_invoice(base_account.account_id, days_overdue=10)
    older_call = CallLogEntry(
        call_id="C1",
        account_id=base_account.account_id,
        called_at=now - timedelta(days=10),
        duration_seconds=60,
        outcome=CallOutcomeType.SOFT_COMMITMENT,
    )
    newer_call = CallLogEntry(
        call_id="C2",
        account_id=base_account.account_id,
        called_at=now - timedelta(days=1),
        duration_seconds=45,
        outcome=CallOutcomeType.WRONG_PERSON,
    )

    pack = build_context_pack(base_account, [inv], [], [], [older_call, newer_call], as_of=as_of)

    assert pack.last_contact_outcome == CallOutcomeType.WRONG_PERSON
    assert pack.last_contact_date == newer_call.called_at


def test_prior_promises_sorted_newest_first(base_account, make_invoice, as_of, now):
    from datetime import timedelta

    inv = make_invoice(base_account.account_id, days_overdue=10)
    older_ptp = PTP(
        ptp_id="PTP-1",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=as_of,
        payment_method=PaymentMethod.NEFT,
        captured_at=now - timedelta(days=20),
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.BROKEN,
        call_id="C1",
    )
    newer_ptp = PTP(
        ptp_id="PTP-2",
        account_id=base_account.account_id,
        invoice_ids=[inv.invoice_id],
        amount_promised=50_000,
        promised_date=as_of,
        payment_method=PaymentMethod.UPI,
        captured_at=now - timedelta(days=2),
        captured_by="v1",
        confidence=0.9,
        status=PTPStatus.OPEN,
        call_id="C2",
    )

    pack = build_context_pack(base_account, [inv], [older_ptp, newer_ptp], [], [], as_of=as_of)

    assert [p.ptp_id for p in pack.prior_promises] == ["PTP-2", "PTP-1"]


def test_ignores_other_accounts(base_account, make_invoice, as_of):
    inv = make_invoice(base_account.account_id, days_overdue=10)
    other_inv = make_invoice("ACC-OTHER", days_overdue=10)

    pack = build_context_pack(base_account, [inv, other_inv], [], [], [], as_of=as_of)

    assert [i.invoice_id for i in pack.invoices] == [inv.invoice_id]
