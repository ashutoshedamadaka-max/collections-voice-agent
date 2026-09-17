"""Tests the Step 5 write-back orchestration against InMemorySheetsBackend (tests/conftest.py)
— no real Sheets network calls. Covers all 4 routing combinations, idempotency, and the two
guards (missing started_at, inconsistent dispute classification).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from collections_agent.models.domain import (
    ComplianceReview,
    DisputeClassification,
    OutcomeExtraction,
    PostCallAnalysis,
    PromiseValidation,
    WriteDecision,
)
from collections_agent.postcall.transcript import Transcript
from collections_agent.postcall.writeback import write_back
from collections_agent.sheets.writers import ensure_all_tabs

STARTED_AT = datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC)

CLEAN_COMPLIANCE = ComplianceReview(
    disclosed_automated=True,
    verified_authority=True,
    stayed_within_permitted_facts=True,
    promised_discount_or_waiver=False,
    threatened_consequences=False,
    qa_score=0.95,
    confidence=0.95,
)


def _transcript(**overrides) -> Transcript:
    fields = dict(
        call_id="call-1",
        turns=[],
        tool_calls=[],
        started_at=STARTED_AT,
        duration_seconds=90.0,
        cost_usd=0.15,
        recording_url="https://example.com/rec.wav",
    )
    fields.update(overrides)
    return Transcript(**fields)


def _analysis(**overrides) -> PostCallAnalysis:
    fields = dict(
        call_id="call-1",
        account_id="ACC-0001",
        outcome=OutcomeExtraction(outcome="promise_to_pay", next_action="follow up", confidence=0.95),
        promise=PromiseValidation(has_promise=False, is_complete=False, confidence=0.95),
        dispute=DisputeClassification(has_dispute=False, confidence=0.95),
        compliance=CLEAN_COMPLIANCE,
        summary="Customer confirmed the invoice, no promise or dispute recorded.",
        overall_confidence=0.95,
        write_decision=WriteDecision.AUTO_WRITE,
    )
    fields.update(overrides)
    return PostCallAnalysis(**fields)


def test_every_call_gets_a_call_log_row_regardless_of_write_decision(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    write_back(fake_sheets_backend, _analysis(write_decision=WriteDecision.EXCEPTION_QUEUE), _transcript())

    rows = fake_sheets_backend.tabs["Call_Log"]
    assert len(rows) == 1
    assert rows[0]["call_id"] == "call-1"
    assert rows[0]["duration_seconds"] == "90"


def test_call_log_recording_url_points_at_fetch_recording_not_a_stored_link(fake_sheets_backend):
    """Vapi's recording links are presigned and expire in ~30 minutes — never store one, even
    if the transcript carries one (see docs/FAILURES.md)."""
    ensure_all_tabs(fake_sheets_backend)
    write_back(fake_sheets_backend, _analysis(), _transcript(recording_url="https://dead-link.example/x.wav"))

    row = fake_sheets_backend.tabs["Call_Log"][0]
    assert row["recording_url"] == "fetch-recording call-1"
    assert "dead-link" not in row["recording_url"]


def test_call_log_carries_the_summary(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    write_back(fake_sheets_backend, _analysis(summary="Agreed to pay by Friday via NEFT."), _transcript())

    row = fake_sheets_backend.tabs["Call_Log"][0]
    assert row["summary"] == "Agreed to pay by Friday via NEFT."


def test_auto_write_complete_promise_writes_ptp_not_soft_commitment(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    promise = PromiseValidation(
        has_promise=True,
        is_complete=True,
        is_future_dated=True,
        amount_within_outstanding=True,
        method_valid=True,
        downgraded_to_soft_commitment=False,
        amount=50000,
        promised_date=STARTED_AT.date(),
        method="NEFT",
        invoice_ids=["INV-1"],
        confidence=0.9,
    )
    write_back(fake_sheets_backend, _analysis(promise=promise), _transcript())

    assert len(fake_sheets_backend.tabs["PTP_Register"]) == 1
    assert fake_sheets_backend.tabs["PTP_Register"][0]["ptp_id"] == "PTP-call-1"
    assert fake_sheets_backend.tabs.get("Soft_Commitments", []) == []


def test_auto_write_downgraded_promise_writes_soft_commitment_not_ptp(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    promise = PromiseValidation(
        has_promise=True, is_complete=False, downgraded_to_soft_commitment=True, confidence=0.9
    )
    write_back(fake_sheets_backend, _analysis(promise=promise), _transcript())

    assert len(fake_sheets_backend.tabs["Soft_Commitments"]) == 1
    assert fake_sheets_backend.tabs["Soft_Commitments"][0]["soft_commitment_id"] == "SC-call-1"
    assert fake_sheets_backend.tabs.get("PTP_Register", []) == []


def test_auto_write_with_dispute_writes_disputes_tab(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    dispute = DisputeClassification(
        has_dispute=True,
        invoice_id="INV-1",
        reason_code="quantity_dispute",
        detail="short shipment",
        routing_target="logistics",
        confidence=0.9,
    )
    write_back(fake_sheets_backend, _analysis(dispute=dispute), _transcript())

    assert len(fake_sheets_backend.tabs["Disputes"]) == 1
    assert fake_sheets_backend.tabs["Disputes"][0]["dispute_id"] == "DSP-call-1"


def test_exception_queue_writes_exceptions_not_ptp_or_disputes(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    analysis = _analysis(
        write_decision=WriteDecision.EXCEPTION_QUEUE,
        supervisor_notes="overall confidence 0.40 below threshold 0.75",
        exception_reason="low_confidence",
        promise=PromiseValidation(has_promise=True, is_complete=True, confidence=0.4),
        dispute=DisputeClassification(has_dispute=True, reason_code="po_mismatch", confidence=0.4),
    )
    write_back(fake_sheets_backend, analysis, _transcript())

    assert len(fake_sheets_backend.tabs["Exceptions"]) == 1
    row = fake_sheets_backend.tabs["Exceptions"][0]
    assert row["reason"] == "low_confidence"
    assert row["resolved"] == ""
    assert fake_sheets_backend.tabs.get("PTP_Register", []) == []
    assert fake_sheets_backend.tabs.get("Disputes", []) == []


def test_rerunning_write_back_is_idempotent_not_duplicated(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    analysis = _analysis(
        promise=PromiseValidation(
            has_promise=True,
            is_complete=True,
            is_future_dated=True,
            amount_within_outstanding=True,
            method_valid=True,
            amount=50000,
            promised_date=STARTED_AT.date(),
            method="NEFT",
            confidence=0.9,
        )
    )
    write_back(fake_sheets_backend, analysis, _transcript())
    write_back(fake_sheets_backend, analysis, _transcript())  # re-run

    assert len(fake_sheets_backend.tabs["Call_Log"]) == 1
    assert len(fake_sheets_backend.tabs["PTP_Register"]) == 1


def test_missing_started_at_raises_instead_of_fabricating_a_timestamp(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    with pytest.raises(ValueError, match="started_at"):
        write_back(fake_sheets_backend, _analysis(), _transcript(started_at=None))


def test_dispute_without_reason_code_raises_instead_of_writing_a_fabricated_one(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    inconsistent_dispute = DisputeClassification(has_dispute=True, reason_code=None, confidence=0.9)
    with pytest.raises(ValueError, match="reason_code"):
        write_back(fake_sheets_backend, _analysis(dispute=inconsistent_dispute), _transcript())


def test_missing_routing_target_falls_back_to_ops(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    dispute = DisputeClassification(
        has_dispute=True, reason_code="cash_flow", routing_target=None, confidence=0.9
    )
    write_back(fake_sheets_backend, _analysis(dispute=dispute), _transcript())

    assert fake_sheets_backend.tabs["Disputes"][0]["routing_target"] == "ops"


class TestReAnalysisReconciliation:
    """2026-09-17: a corrected re-analysis of the same call_id can flip write_decision, promise
    completeness, or dispute presence between runs. SheetsBackend has no delete verb, so a
    stale row from the first run must be marked superseded/resolved, not left silently
    contradicting the corrected one (see docs/FAILURES.md and writeback.py's module docstring).
    """

    _COMPLETE_PROMISE = PromiseValidation(
        has_promise=True,
        is_complete=True,
        is_future_dated=True,
        amount_within_outstanding=True,
        method_valid=True,
        downgraded_to_soft_commitment=False,
        amount=50000,
        promised_date=STARTED_AT.date(),
        method="NEFT",
        invoice_ids=["INV-1"],
        confidence=0.9,
    )
    _REAL_DISPUTE = DisputeClassification(
        has_dispute=True,
        invoice_id="INV-1",
        reason_code="quantity_dispute",
        detail="short shipment",
        routing_target="logistics",
        confidence=0.9,
    )

    def test_exception_resolved_when_reanalysis_auto_writes(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        first = _analysis(
            write_decision=WriteDecision.EXCEPTION_QUEUE,
            exception_reason="compliance_violation",
            supervisor_notes="agent promised a discount or waiver",
        )
        write_back(fake_sheets_backend, first, _transcript())
        assert fake_sheets_backend.tabs["Exceptions"][0]["resolved"] == ""

        corrected = _analysis(promise=self._COMPLETE_PROMISE)
        write_back(fake_sheets_backend, corrected, _transcript())

        exception_row = fake_sheets_backend.tabs["Exceptions"][0]
        assert "resolved_by_reanalysis" in exception_row["resolved"]
        assert len(fake_sheets_backend.tabs["PTP_Register"]) == 1
        assert fake_sheets_backend.tabs["PTP_Register"][0]["status"] == "open"

    def test_a_human_resolved_exception_is_never_overwritten(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        first = _analysis(write_decision=WriteDecision.EXCEPTION_QUEUE)
        write_back(fake_sheets_backend, first, _transcript())
        # A human resolves it by hand directly in the sheet before any re-analysis happens.
        fake_sheets_backend.tabs["Exceptions"][0]["resolved"] = "handled manually — see notes"

        corrected = _analysis(promise=self._COMPLETE_PROMISE)
        write_back(fake_sheets_backend, corrected, _transcript())

        assert fake_sheets_backend.tabs["Exceptions"][0]["resolved"] == "handled manually — see notes"

    def test_ptp_superseded_when_reanalysis_routes_to_exception_queue(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        write_back(fake_sheets_backend, _analysis(promise=self._COMPLETE_PROMISE), _transcript())
        assert fake_sheets_backend.tabs["PTP_Register"][0]["status"] == "open"

        corrected = _analysis(write_decision=WriteDecision.EXCEPTION_QUEUE)
        write_back(fake_sheets_backend, corrected, _transcript())

        assert fake_sheets_backend.tabs["PTP_Register"][0]["status"] == "superseded"
        assert len(fake_sheets_backend.tabs["Exceptions"]) == 1

    def test_ptp_superseded_when_reanalysis_downgrades_to_soft_commitment(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        write_back(fake_sheets_backend, _analysis(promise=self._COMPLETE_PROMISE), _transcript())

        downgraded = PromiseValidation(
            has_promise=True, is_complete=False, downgraded_to_soft_commitment=True, confidence=0.9
        )
        write_back(fake_sheets_backend, _analysis(promise=downgraded), _transcript())

        assert fake_sheets_backend.tabs["PTP_Register"][0]["status"] == "superseded"
        assert len(fake_sheets_backend.tabs["Soft_Commitments"]) == 1
        assert fake_sheets_backend.tabs["Soft_Commitments"][0]["superseded"] == "False"

    def test_dispute_superseded_when_reanalysis_finds_no_dispute(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        write_back(fake_sheets_backend, _analysis(dispute=self._REAL_DISPUTE), _transcript())
        assert fake_sheets_backend.tabs["Disputes"][0]["status"] == "open"

        no_dispute = DisputeClassification(has_dispute=False, confidence=0.9)
        write_back(fake_sheets_backend, _analysis(dispute=no_dispute), _transcript())

        assert fake_sheets_backend.tabs["Disputes"][0]["status"] == "superseded"

    def test_rerunning_the_same_decision_does_not_supersede_anything(self, fake_sheets_backend):
        ensure_all_tabs(fake_sheets_backend)
        analysis = _analysis(promise=self._COMPLETE_PROMISE, dispute=self._REAL_DISPUTE)
        write_back(fake_sheets_backend, analysis, _transcript())
        write_back(fake_sheets_backend, analysis, _transcript())

        assert fake_sheets_backend.tabs["PTP_Register"][0]["status"] == "open"
        assert fake_sheets_backend.tabs["Disputes"][0]["status"] == "open"
        assert len(fake_sheets_backend.tabs["PTP_Register"]) == 1
        assert len(fake_sheets_backend.tabs["Disputes"]) == 1
