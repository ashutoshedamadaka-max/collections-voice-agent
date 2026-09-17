"""Each specialist is a thin prompt + extract_structured call, possibly followed by pure-code
arithmetic (promise, compliance — see docs/FAILURES.md for why the arithmetic moved out of the
model). These tests monkeypatch extract_structured itself (no real OpenAI call).
"""

from __future__ import annotations

from datetime import date

import pytest

from collections_agent.models.domain import (
    CallSummary,
    ComplianceExtraction,
    DisputeClassification,
    OutcomeExtraction,
    PromiseExtraction,
)
from collections_agent.postcall import openai_client as openai_client_module
from collections_agent.postcall.specialists import compliance, dispute, outcome, promise, summary
from collections_agent.postcall.transcript import ToolCallRecord, Transcript, TranscriptTurn

TRANSCRIPT = Transcript(
    call_id="call-1",
    turns=[
        TranscriptTurn(role="assistant", content="Hello, this is an automated call..."),
        TranscriptTurn(role="user", content="Yes, I'll pay 50000 by NEFT on 2026-10-01."),
    ],
    tool_calls=[
        ToolCallRecord(
            name="record_ptp",
            arguments={"invoice_ids": ["INV-1"], "amount": 50000, "date": "2026-10-01", "method": "NEFT"},
            result={"ptp_id": "PTP-1"},
        )
    ],
)


@pytest.fixture
def capture_call(monkeypatch):
    """Patches extract_structured in the given specialist module, returns a dict the test can
    inspect for what the specialist sent, and a canned return value the test controls."""

    def _patch(module, return_value):
        captured = {}

        def fake_extract_structured(system_prompt, user_content, schema, api_key, **kwargs):
            captured["system_prompt"] = system_prompt
            captured["user_content"] = user_content
            captured["schema"] = schema
            captured["api_key"] = api_key
            return return_value

        monkeypatch.setattr(module, "extract_structured", fake_extract_structured)
        return captured

    return _patch


def test_extract_outcome_returns_specialist_result(capture_call):
    expected = OutcomeExtraction(
        outcome="promise_to_pay", next_action="follow up on 2026-10-01", confidence=0.9
    )
    captured = capture_call(outcome, expected)

    result = outcome.extract_outcome(TRANSCRIPT, api_key="key-123")

    assert result == expected
    assert captured["schema"] is OutcomeExtraction
    assert "50000" in captured["user_content"]


def test_extract_promise_only_sees_the_transcript(capture_call):
    """No invoice/arithmetic context goes to the model at all now — see module docstring."""
    expected = PromiseExtraction(has_promise=True, confidence=0.95)
    captured = capture_call(promise, expected)

    result = promise.extract_promise(TRANSCRIPT, api_key="key-123")

    assert result == expected
    assert captured["schema"] is PromiseExtraction
    assert "50000" in captured["user_content"]


class TestValidatePromiseFacts:
    """validate_promise_facts is pure code — no LLM, no monkeypatching needed."""

    def test_complete_future_dated_amount_exactly_at_outstanding_is_not_downgraded(
        self, make_invoice, as_of
    ):
        inv1 = make_invoice("ACC-1", amount=85_000, days_overdue=10)
        inv2 = make_invoice("ACC-1", amount=280_500, days_overdue=10)
        extraction = PromiseExtraction(
            has_promise=True,
            amount=365_500,
            promised_date=date(2026, 10, 15),
            method="NEFT",
            invoice_ids=[inv1.invoice_id, inv2.invoice_id],
            confidence=0.9,
        )

        result = promise.validate_promise_facts(extraction, [inv1, inv2], as_of)

        assert result.amount_within_outstanding is True, (
            "365,500 is exactly 85,000 + 280,500 — this is the exact bug an LLM got wrong"
        )
        assert result.is_future_dated is True
        assert result.downgraded_to_soft_commitment is False

    def test_past_date_is_downgraded(self, as_of):
        extraction = PromiseExtraction(
            has_promise=True, amount=1000, promised_date=date(2020, 1, 1), method="NEFT", confidence=0.9
        )

        result = promise.validate_promise_facts(extraction, [], as_of)

        assert result.is_future_dated is False
        assert result.downgraded_to_soft_commitment is True

    def test_amount_over_outstanding_is_downgraded(self, make_invoice, as_of):
        inv = make_invoice("ACC-1", amount=1000, days_overdue=10)
        extraction = PromiseExtraction(
            has_promise=True,
            amount=1_000_000,
            promised_date=date(2026, 12, 1),
            method="NEFT",
            invoice_ids=[inv.invoice_id],
            confidence=0.9,
        )

        result = promise.validate_promise_facts(extraction, [inv], as_of)

        assert result.amount_within_outstanding is False
        assert result.downgraded_to_soft_commitment is True

    def test_missing_fields_are_incomplete_and_downgraded(self, as_of):
        extraction = PromiseExtraction(has_promise=True, confidence=0.5)

        result = promise.validate_promise_facts(extraction, [], as_of)

        assert result.is_complete is False
        assert result.downgraded_to_soft_commitment is True

    def test_no_promise_at_all_is_not_downgraded(self, as_of):
        """Nothing to downgrade if nothing was promised."""
        extraction = PromiseExtraction(has_promise=False, confidence=0.9)

        result = promise.validate_promise_facts(extraction, [], as_of)

        assert result.downgraded_to_soft_commitment is False

    def test_matches_invoice_by_number_or_id(self, make_invoice, as_of):
        inv = make_invoice("ACC-1", amount=100_000, days_overdue=10)
        extraction = PromiseExtraction(
            has_promise=True,
            amount=100_000,
            promised_date=date(2026, 12, 1),
            method="NEFT",
            invoice_ids=[inv.invoice_number],  # spoken form, not the internal id
            confidence=0.9,
        )

        result = promise.validate_promise_facts(extraction, [inv], as_of)

        assert result.amount_within_outstanding is True


def test_classify_dispute_returns_specialist_result(capture_call):
    expected = DisputeClassification(has_dispute=False, confidence=0.9)
    captured = capture_call(dispute, expected)

    result = dispute.classify_dispute(TRANSCRIPT, api_key="key-123")

    assert result == expected
    assert captured["schema"] is DisputeClassification


def test_extract_summary_returns_specialist_result(capture_call):
    expected = CallSummary(summary="Customer agreed to pay 50000 by NEFT on 2026-10-01.")
    captured = capture_call(summary, expected)

    result = summary.extract_summary(TRANSCRIPT, api_key="key-123")

    assert result == expected
    assert captured["schema"] is CallSummary
    assert "50000" in captured["user_content"]


CLEAN_EXTRACTION = ComplianceExtraction(
    disclosed_automated=True,
    verified_authority=True,
    stayed_within_permitted_facts=True,
    promised_discount_or_waiver=False,
    threatened_consequences=False,
    qa_score=1.0,
    confidence=0.9,
)


def test_review_compliance_returns_llm_judgment_when_totals_match(capture_call, make_invoice, as_of):
    inv = make_invoice("ACC-1", amount=100_000, days_overdue=10)
    captured = capture_call(compliance, CLEAN_EXTRACTION)

    result = compliance.review_compliance(TRANSCRIPT, [inv], api_key="key-123")

    assert result.qa_score == 1.0
    assert result.misstated_total is False
    assert captured["schema"] is ComplianceExtraction


def test_review_compliance_caps_qa_score_on_misstated_total(capture_call, make_invoice, as_of):
    inv = make_invoice("ACC-1", amount=100_000, days_overdue=10)
    transcript = Transcript(
        call_id="call-2",
        turns=[TranscriptTurn(role="bot", content="The total outstanding amount is 999,000.")],
        tool_calls=[],
    )
    capture_call(compliance, CLEAN_EXTRACTION)

    result = compliance.review_compliance(transcript, [inv], api_key="key-123")

    assert result.misstated_total is True
    assert result.qa_score <= 0.5
    assert "999000.0" in result.notes or "999,000" in result.notes or "arithmetic" in result.notes.lower()


class TestCheckArithmeticConsistency:
    """Pure code — no LLM, no monkeypatching."""

    def test_no_invoices_never_flags(self):
        transcript = Transcript(
            call_id="c", turns=[TranscriptTurn(role="bot", content="Total is 500.")], tool_calls=[]
        )
        misstated, _ = compliance.check_arithmetic_consistency(transcript, [])
        assert misstated is False

    def test_matching_total_does_not_flag(self, make_invoice, as_of):
        inv = make_invoice("ACC-1", amount=365_500, days_overdue=10)
        transcript = Transcript(
            call_id="c",
            turns=[TranscriptTurn(role="bot", content="The total outstanding amount is 365,500.")],
            tool_calls=[],
        )
        misstated, _ = compliance.check_arithmetic_consistency(transcript, [inv])
        assert misstated is False

    def test_mismatched_total_flags_with_detail(self, make_invoice, as_of):
        inv = make_invoice("ACC-1", amount=365_500, days_overdue=10)
        transcript = Transcript(
            call_id="c",
            turns=[TranscriptTurn(role="bot", content="The total outstanding amount is 840,000.")],
            tool_calls=[],
        )
        misstated, detail = compliance.check_arithmetic_consistency(transcript, [inv])
        assert misstated is True
        assert "840000" in detail.replace(",", "").replace(".0", "")

    def test_no_total_mentioned_does_not_flag(self, make_invoice, as_of):
        inv = make_invoice("ACC-1", amount=365_500, days_overdue=10)
        transcript = Transcript(
            call_id="c",
            turns=[TranscriptTurn(role="bot", content="Can you confirm your name?")],
            tool_calls=[],
        )
        misstated, _ = compliance.check_arithmetic_consistency(transcript, [inv])
        assert misstated is False

    def test_garbled_digit_fragment_is_unverifiable_not_a_violation(self, make_invoice, as_of):
        """A pre-fix real transcript produced 'total ... 3' from digit-by-digit garbled STT
        output ("3 6 5 5. 0 0 0. 0 0" for 365,500) — the regex grabbed the lone leading "3".
        That's a transcription artifact, not a stated figure, and must not be flagged."""
        inv = make_invoice("ACC-1", amount=365_500, days_overdue=10)
        transcript = Transcript(
            call_id="c",
            turns=[TranscriptTurn(role="bot", content="The total outstanding is 3 6 5 5. 0 0 0. 0 0")],
            tool_calls=[],
        )
        misstated, detail = compliance.check_arithmetic_consistency(transcript, [inv])
        assert misstated is False
        assert "could not verify" in detail

    def test_no_invoices_reports_unverifiable_detail(self):
        transcript = Transcript(
            call_id="c", turns=[TranscriptTurn(role="bot", content="Total is 500,000.")], tool_calls=[]
        )
        misstated, detail = compliance.check_arithmetic_consistency(transcript, [])
        assert misstated is False
        assert "could not verify" in detail


def test_openai_client_module_is_the_single_call_site():
    """If this ever fails, a specialist started calling OpenAI directly instead of through
    the isolated wrapper — exactly the kind of API-drift risk openai_client.py exists to
    contain in one place."""
    import inspect

    for module in (outcome, promise, dispute, compliance, summary):
        source = inspect.getsource(module)
        assert "OpenAI(" not in source
        assert openai_client_module.extract_structured.__name__ in source
