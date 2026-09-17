"""Tests compute_metrics against small synthetic Call_Log/PTP_Register/Disputes/Exceptions
sets — pure function, no Sheets involved."""

from __future__ import annotations

from datetime import UTC, date, datetime

from collections_agent.metrics.rollup import compute_metrics
from collections_agent.models.domain import PTP, CallLogEntry, Dispute, ExceptionEntry

TARGET_DATE = date(2026, 9, 17)
OTHER_DATE = date(2026, 9, 16)


def _call(outcome, on=TARGET_DATE, call_id="C1") -> CallLogEntry:
    return CallLogEntry(
        call_id=call_id,
        account_id="ACC-0001",
        called_at=datetime.combine(on, datetime.min.time(), tzinfo=UTC),
        duration_seconds=60,
        outcome=outcome,
    )


def _ptp(status, amount=50_000, ptp_id="PTP-1") -> PTP:
    return PTP(
        ptp_id=ptp_id,
        account_id="ACC-0001",
        invoice_ids=["INV-1"],
        amount_promised=amount,
        promised_date=date(2026, 9, 1),
        payment_method="NEFT",
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        captured_by="v1",
        confidence=0.9,
        status=status,
        call_id="C1",
    )


def test_calls_made_counts_only_target_date():
    calls = [_call("promise_to_pay", on=TARGET_DATE), _call("promise_to_pay", on=OTHER_DATE, call_id="C2")]
    row = compute_metrics(calls, [], [], [], TARGET_DATE)
    assert row.calls_made == 1


def test_calls_suppressed_is_honestly_zero_not_fabricated():
    row = compute_metrics([], [], [], [], TARGET_DATE)
    assert row.calls_suppressed == 0


def test_contact_rate_excludes_no_answer_only():
    calls = [
        _call("no_answer", call_id="C1"),
        _call("hostile", call_id="C2"),
        _call("wrong_person", call_id="C3"),
        _call("promise_to_pay", call_id="C4"),
    ]
    row = compute_metrics(calls, [], [], [], TARGET_DATE)
    assert row.contact_rate == 0.75  # 3 of 4 — only no_answer excluded


def test_structured_outcome_rate_excludes_no_answer_hostile_and_wrong_person():
    calls = [
        _call("no_answer", call_id="C1"),
        _call("hostile", call_id="C2"),
        _call("wrong_person", call_id="C3"),
        _call("promise_to_pay", call_id="C4"),
    ]
    row = compute_metrics(calls, [], [], [], TARGET_DATE)
    assert row.structured_outcome_rate == 0.25  # only 1 of 4


def test_kept_rate_excludes_open_promises():
    ptps = [_ptp("kept", ptp_id="PTP-1"), _ptp("open", ptp_id="PTP-2")]
    row = compute_metrics([], ptps, [], [], TARGET_DATE)
    assert row.promise_to_pay_kept_rate == 1.0  # only the kept one is in the denominator


def test_kept_rate_excludes_superseded():
    ptps = [_ptp("kept", ptp_id="PTP-1"), _ptp("superseded", ptp_id="PTP-2")]
    row = compute_metrics([], ptps, [], [], TARGET_DATE)
    assert row.promise_to_pay_kept_rate == 1.0


def test_partial_counts_in_denominator_not_numerator():
    ptps = [_ptp("kept", ptp_id="PTP-1"), _ptp("partial", ptp_id="PTP-2")]
    row = compute_metrics([], ptps, [], [], TARGET_DATE)
    assert row.promise_to_pay_kept_rate == 0.5  # 1 kept / (1 kept + 1 partial)


def test_count_based_and_value_weighted_kept_rate_can_diverge():
    """The whole point of the rupee-weighted metric: a kept small promise and a broken large
    one shouldn't read as a healthy 50%."""
    ptps = [_ptp("kept", amount=5_000, ptp_id="PTP-1"), _ptp("broken", amount=500_000, ptp_id="PTP-2")]
    row = compute_metrics([], ptps, [], [], TARGET_DATE)
    assert row.promise_to_pay_kept_rate == 0.5
    assert row.promise_to_pay_kept_rate_by_value == round(5_000 / 505_000, 4)


def test_disputes_surfaced_counts_only_target_date():
    disputes = [
        Dispute(
            dispute_id="DSP-1",
            account_id="ACC-0001",
            invoice_id="INV-1",
            reason_code="quantity_dispute",
            detail="x",
            routing_target="logistics",
            opened_at=datetime.combine(TARGET_DATE, datetime.min.time(), tzinfo=UTC),
            call_id="C1",
        ),
        Dispute(
            dispute_id="DSP-2",
            account_id="ACC-0001",
            invoice_id="INV-1",
            reason_code="po_mismatch",
            detail="y",
            routing_target="sales",
            opened_at=datetime.combine(OTHER_DATE, datetime.min.time(), tzinfo=UTC),
            call_id="C2",
        ),
    ]
    row = compute_metrics([], [], disputes, [], TARGET_DATE)
    assert row.disputes_surfaced == 1


def test_human_review_rate():
    calls = [_call("promise_to_pay", call_id="C1"), _call("dispute", call_id="C2")]
    exceptions = [
        ExceptionEntry(
            call_id="C1",
            account_id="ACC-0001",
            reason="low_confidence",
            supervisor_notes="x",
            created_at=datetime.combine(TARGET_DATE, datetime.min.time(), tzinfo=UTC),
        )
    ]
    row = compute_metrics(calls, [], [], exceptions, TARGET_DATE)
    assert row.human_review_rate == 0.5


def test_zero_calls_made_does_not_divide_by_zero():
    row = compute_metrics([], [], [], [], TARGET_DATE)
    assert row.contact_rate == 0.0
    assert row.structured_outcome_rate == 0.0
    assert row.human_review_rate == 0.0


def test_no_resolved_ptps_gives_zero_kept_rate_not_a_crash():
    row = compute_metrics([], [_ptp("open")], [], [], TARGET_DATE)
    assert row.promise_to_pay_kept_rate == 0.0
    assert row.promise_to_pay_kept_rate_by_value == 0.0
