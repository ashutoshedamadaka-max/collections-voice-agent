"""Round-trips Account/Invoice through the same fake backend used elsewhere
(tests/conftest.py's InMemorySheetsBackend) via the real writers, then reads them back —
this is exactly the write-then-read path the real Sheet goes through, just without the
network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from collections_agent.models.domain import (
    PTP,
    CallLogEntry,
    Dispute,
    ExceptionEntry,
    Payment,
    SoftCommitment,
)
from collections_agent.sheets.readers import (
    read_accounts,
    read_call_log,
    read_disputes,
    read_exceptions,
    read_invoices,
    read_payments,
    read_ptps,
    read_soft_commitments,
)
from collections_agent.sheets.writers import (
    ensure_all_tabs,
    seed_accounts,
    seed_invoices,
    write_call_log,
    write_disputes,
    write_exceptions,
    write_payments,
    write_ptps,
    write_soft_commitments,
)


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


def test_round_trips_ptp_with_multiple_invoice_ids(fake_sheets_backend):
    """PTP.invoice_ids is a list — writers.py joins it with ';' on write; this is the exact
    round-trip bug class this file exists to catch (see docs/FAILURES.md's Step 6 plan)."""
    ptp = PTP(
        ptp_id="PTP-1",
        account_id="ACC-0001",
        invoice_ids=["INV-1", "INV-2"],
        amount_promised=50_000,
        promised_date=date(2026, 10, 1),
        payment_method="NEFT",
        captured_at=datetime(2026, 9, 17, tzinfo=UTC),
        captured_by="v1",
        confidence=0.9,
        call_id="C1",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_ptps(fake_sheets_backend, [ptp])

    result = read_ptps(fake_sheets_backend)

    assert len(result) == 1
    assert result[0] == ptp


def test_round_trips_ptp_with_empty_invoice_ids_as_empty_list(fake_sheets_backend):
    """An empty invoice_ids list must round-trip to [], not [""]."""
    ptp = PTP(
        ptp_id="PTP-2",
        account_id="ACC-0001",
        invoice_ids=[],
        amount_promised=50_000,
        promised_date=date(2026, 10, 1),
        payment_method="NEFT",
        captured_at=datetime(2026, 9, 17, tzinfo=UTC),
        captured_by="v1",
        confidence=0.9,
        call_id="C2",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_ptps(fake_sheets_backend, [ptp])

    result = read_ptps(fake_sheets_backend)

    assert result[0].invoice_ids == []


def test_round_trips_soft_commitment(fake_sheets_backend):
    sc = SoftCommitment(
        soft_commitment_id="SC-1",
        account_id="ACC-0001",
        invoice_ids=["INV-1"],
        note="will pay once revised invoice sent",
        captured_at=datetime(2026, 9, 17, tzinfo=UTC),
        captured_by="v1",
        call_id="C1",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_soft_commitments(fake_sheets_backend, [sc])
    assert read_soft_commitments(fake_sheets_backend) == [sc]


def test_round_trips_call_log(fake_sheets_backend):
    entry = CallLogEntry(
        call_id="C1",
        account_id="ACC-0001",
        called_at=datetime(2026, 9, 17, tzinfo=UTC),
        duration_seconds=90,
        outcome="promise_to_pay",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_call_log(fake_sheets_backend, [entry])
    assert read_call_log(fake_sheets_backend) == [entry]


def test_optional_non_bool_non_list_field_round_trips_to_none(fake_sheets_backend):
    """Regression test: _coerce_row originally only special-cased bool and list fields, so an
    Optional[str]/Optional[float]/Optional[enum] field written as "" (a real None) came back
    as a literal "" instead of None — for enum/float fields this crashed validation outright,
    for str fields it silently mismatched. Caught by test_round_trips_disputes' equality
    assertion, not by any crash (see docs/FAILURES.md)."""
    dispute = Dispute(
        dispute_id="DSP-none-check",
        account_id="ACC-0001",
        invoice_id="INV-1",
        reason_code="quantity_dispute",
        detail="short shipment",
        evidence_requested=None,
        routing_target="logistics",
        opened_at=datetime(2026, 9, 17, tzinfo=UTC),
        call_id="C1",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_disputes(fake_sheets_backend, [dispute])

    result = read_disputes(fake_sheets_backend)[0]

    assert result.evidence_requested is None


def test_round_trips_disputes(fake_sheets_backend):
    dispute = Dispute(
        dispute_id="DSP-1",
        account_id="ACC-0001",
        invoice_id="INV-1",
        reason_code="quantity_dispute",
        detail="short shipment",
        routing_target="logistics",
        opened_at=datetime(2026, 9, 17, tzinfo=UTC),
        call_id="C1",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_disputes(fake_sheets_backend, [dispute])
    assert read_disputes(fake_sheets_backend) == [dispute]


def test_round_trips_exceptions(fake_sheets_backend):
    entry = ExceptionEntry(
        call_id="C1",
        account_id="ACC-0001",
        reason="low_confidence",
        supervisor_notes="overall confidence 0.40 below threshold 0.75",
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    ensure_all_tabs(fake_sheets_backend)
    write_exceptions(fake_sheets_backend, [entry])
    assert read_exceptions(fake_sheets_backend) == [entry]


def test_round_trips_payments(fake_sheets_backend):
    payment = Payment(
        invoice_id="INV-1",
        amount_paid=50_000,
        paid_date=date(2026, 10, 1),
        method="NEFT",
        reference="UTR123",
    )
    ensure_all_tabs(fake_sheets_backend)
    write_payments(fake_sheets_backend, [payment])
    assert read_payments(fake_sheets_backend) == [payment]
