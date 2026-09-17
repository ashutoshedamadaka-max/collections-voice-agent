"""Tests the supervisor's merge logic — confidence-gating and cross-specialist disagreement
detection — by monkeypatching all four specialist functions with canned results. Nothing here
calls OpenAI; that boundary is already covered by test_specialists.py and test_openai_client.py.
"""

from __future__ import annotations

from collections_agent.models.domain import (
    CallSummary,
    ComplianceReview,
    DisputeClassification,
    OutcomeExtraction,
    PromiseValidation,
    WriteDecision,
)
from collections_agent.postcall import pipeline
from collections_agent.postcall.transcript import Transcript

TRANSCRIPT = Transcript(call_id="call-1", turns=[], tool_calls=[])

HIGH_CONFIDENCE_PROMISE_TO_PAY = OutcomeExtraction(
    outcome="promise_to_pay", next_action="follow up 2026-10-01", confidence=0.95
)
HIGH_CONFIDENCE_PROMISE = PromiseValidation(has_promise=True, is_complete=True, confidence=0.95)
NO_DISPUTE = DisputeClassification(has_dispute=False, confidence=0.95)
CLEAN_COMPLIANCE = ComplianceReview(
    disclosed_automated=True,
    verified_authority=True,
    stayed_within_permitted_facts=True,
    promised_discount_or_waiver=False,
    threatened_consequences=False,
    qa_score=1.0,
    confidence=0.95,
)
DEFAULT_SUMMARY = CallSummary(summary="Customer agreed to pay in full by 2026-10-01 via NEFT.")


def _patch_specialists(
    monkeypatch, *, outcome=None, promise=None, dispute=None, compliance=None, summary=None
):
    monkeypatch.setattr(
        pipeline, "extract_outcome", lambda *a, **k: outcome or HIGH_CONFIDENCE_PROMISE_TO_PAY
    )
    monkeypatch.setattr(pipeline, "validate_promise", lambda *a, **k: promise or HIGH_CONFIDENCE_PROMISE)
    monkeypatch.setattr(pipeline, "classify_dispute", lambda *a, **k: dispute or NO_DISPUTE)
    monkeypatch.setattr(pipeline, "review_compliance", lambda *a, **k: compliance or CLEAN_COMPLIANCE)
    monkeypatch.setattr(pipeline, "extract_summary", lambda *a, **k: summary or DEFAULT_SUMMARY)


def _run(monkeypatch, *, confidence_threshold=0.75, **specialist_overrides):
    _patch_specialists(monkeypatch, **specialist_overrides)
    return pipeline.run_postcall(
        call_id="call-1",
        account_id="ACC-0001",
        transcript=TRANSCRIPT,
        invoices=[],
        as_of=None,
        api_key="key-123",
        confidence_threshold=confidence_threshold,
    )


def test_all_high_confidence_no_disagreement_auto_writes(monkeypatch):
    analysis = _run(monkeypatch)
    assert analysis.write_decision == WriteDecision.AUTO_WRITE
    assert analysis.supervisor_notes == ""
    assert analysis.overall_confidence == 0.95


def test_low_confidence_on_immaterial_no_dispute_does_not_drag_down_overall(monkeypatch):
    """A dispute specialist confidently or unconfidently finding NOTHING doesn't matter to
    this call's write-back — nothing dispute-related gets written either way. min()-across-all
    would wrongly route every clean call to exception_queue on a scale-misuse specialist
    (see docs/FAILURES.md); materiality fixes that."""
    low_confidence_no_dispute = DisputeClassification(has_dispute=False, confidence=0.1)
    analysis = _run(monkeypatch, dispute=low_confidence_no_dispute)
    assert analysis.overall_confidence == 0.95
    assert analysis.write_decision == WriteDecision.AUTO_WRITE


def test_low_confidence_on_a_material_dispute_does_drag_down_overall(monkeypatch):
    """Once the dispute specialist actually found something to write back, its confidence in
    that finding is exactly what should gate the call."""
    unsure_real_dispute = DisputeClassification(has_dispute=True, confidence=0.3)
    analysis = _run(monkeypatch, dispute=unsure_real_dispute)
    assert analysis.overall_confidence == 0.3
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE


def test_low_confidence_on_immaterial_no_promise_does_not_drag_down_overall(monkeypatch):
    # outcome deliberately not promise_to_pay here, so the only thing under test is confidence
    # materiality — a promise_to_pay outcome with has_promise=False is a separate disagreement.
    other_outcome = OutcomeExtraction(outcome="dispute", next_action="route it", confidence=0.95)
    real_dispute = DisputeClassification(has_dispute=True, confidence=0.95)
    low_confidence_no_promise = PromiseValidation(has_promise=False, is_complete=False, confidence=0.1)

    analysis = _run(
        monkeypatch, outcome=other_outcome, dispute=real_dispute, promise=low_confidence_no_promise
    )

    assert analysis.overall_confidence == 0.95
    assert analysis.write_decision == WriteDecision.AUTO_WRITE


def test_outcome_and_compliance_confidence_always_count(monkeypatch):
    """Unlike promise/dispute, these are always material — every call gets a Call_Log row and
    a QA score regardless of what else happened."""
    low_confidence_compliance = ComplianceReview(
        disclosed_automated=True,
        verified_authority=True,
        stayed_within_permitted_facts=True,
        promised_discount_or_waiver=False,
        threatened_consequences=False,
        qa_score=1.0,
        confidence=0.2,
    )
    analysis = _run(monkeypatch, compliance=low_confidence_compliance)
    assert analysis.overall_confidence == 0.2
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE


def test_below_threshold_confidence_routes_to_exception_queue(monkeypatch):
    low_confidence_outcome = OutcomeExtraction(
        outcome="promise_to_pay", next_action="unclear", confidence=0.5
    )
    analysis = _run(monkeypatch, outcome=low_confidence_outcome)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "confidence" in analysis.supervisor_notes


def test_outcome_promise_but_no_promise_found_is_a_disagreement(monkeypatch):
    no_promise = PromiseValidation(has_promise=False, is_complete=False, confidence=0.95)
    analysis = _run(monkeypatch, promise=no_promise)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "no promise" in analysis.supervisor_notes


def test_outcome_promise_but_downgraded_to_soft_commitment_is_a_disagreement(monkeypatch):
    downgraded = PromiseValidation(
        has_promise=True, is_complete=False, downgraded_to_soft_commitment=True, confidence=0.95
    )
    analysis = _run(monkeypatch, promise=downgraded)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "downgraded" in analysis.supervisor_notes


def test_outcome_dispute_but_no_dispute_found_is_a_disagreement(monkeypatch):
    dispute_outcome = OutcomeExtraction(outcome="dispute", next_action="route it", confidence=0.95)
    analysis = _run(monkeypatch, outcome=dispute_outcome)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "dispute" in analysis.supervisor_notes


def test_consistent_dispute_outcome_does_not_flag_a_disagreement(monkeypatch):
    dispute_outcome = OutcomeExtraction(outcome="dispute", next_action="route it", confidence=0.95)
    real_dispute = DisputeClassification(has_dispute=True, confidence=0.95)
    analysis = _run(monkeypatch, outcome=dispute_outcome, dispute=real_dispute)
    assert analysis.write_decision == WriteDecision.AUTO_WRITE


def test_hard_violation_forces_exception_queue_even_at_full_confidence(monkeypatch):
    discount_promised = ComplianceReview(
        disclosed_automated=True,
        verified_authority=True,
        stayed_within_permitted_facts=True,
        promised_discount_or_waiver=True,
        threatened_consequences=False,
        qa_score=0.9,
        confidence=1.0,
    )
    analysis = _run(monkeypatch, compliance=discount_promised)
    assert analysis.overall_confidence >= 0.95  # every specialist was confident
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "discount" in analysis.supervisor_notes


def test_misstated_total_forces_exception_queue(monkeypatch):
    misstated = ComplianceReview(
        disclosed_automated=True,
        verified_authority=True,
        stayed_within_permitted_facts=True,
        promised_discount_or_waiver=False,
        threatened_consequences=False,
        misstated_total=True,
        qa_score=0.5,
        confidence=1.0,
    )
    analysis = _run(monkeypatch, compliance=misstated)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "misstated" in analysis.supervisor_notes


def test_failed_authority_gate_forces_exception_queue(monkeypatch):
    no_authority_check = ComplianceReview(
        disclosed_automated=True,
        verified_authority=False,
        stayed_within_permitted_facts=True,
        promised_discount_or_waiver=False,
        threatened_consequences=False,
        qa_score=0.5,
        confidence=1.0,
    )
    analysis = _run(monkeypatch, compliance=no_authority_check)
    assert analysis.write_decision == WriteDecision.EXCEPTION_QUEUE
    assert "authority" in analysis.supervisor_notes


def test_clean_compliance_does_not_force_exception_queue(monkeypatch):
    analysis = _run(monkeypatch)  # CLEAN_COMPLIANCE default
    assert analysis.write_decision == WriteDecision.AUTO_WRITE


def test_result_carries_the_call_id_and_all_five_specialist_outputs(monkeypatch):
    analysis = _run(monkeypatch)
    assert analysis.call_id == "call-1"
    assert analysis.account_id == "ACC-0001"
    assert analysis.outcome == HIGH_CONFIDENCE_PROMISE_TO_PAY
    assert analysis.promise == HIGH_CONFIDENCE_PROMISE
    assert analysis.dispute == NO_DISPUTE
    assert analysis.compliance == CLEAN_COMPLIANCE
    assert analysis.summary == DEFAULT_SUMMARY.summary


def test_summary_never_gates_write_decision(monkeypatch):
    """Purely descriptive — unlike the other four, a summary's own confidence isn't even a
    field, and it must never be able to force exception_queue on its own."""
    analysis = _run(monkeypatch, summary=CallSummary(summary="Vague or unhelpful summary text."))
    assert analysis.write_decision == WriteDecision.AUTO_WRITE


class TestExceptionReason:
    """The Exceptions tab's `reason` column — short structured tags, not a re-parse of
    supervisor_notes free text."""

    def test_clean_call_has_empty_exception_reason(self, monkeypatch):
        analysis = _run(monkeypatch)
        assert analysis.exception_reason == ""

    def test_low_confidence_tags_low_confidence(self, monkeypatch):
        low = OutcomeExtraction(outcome="dispute", next_action="x", confidence=0.3)
        real_dispute = DisputeClassification(has_dispute=True, confidence=0.95)
        analysis = _run(monkeypatch, outcome=low, dispute=real_dispute)
        assert analysis.exception_reason == "low_confidence"

    def test_disagreement_tags_disagreement(self, monkeypatch):
        no_promise = PromiseValidation(has_promise=False, is_complete=False, confidence=0.95)
        analysis = _run(monkeypatch, promise=no_promise)
        assert analysis.exception_reason == "disagreement"

    def test_hard_violation_tags_compliance_violation(self, monkeypatch):
        discount = ComplianceReview(
            disclosed_automated=True,
            verified_authority=True,
            stayed_within_permitted_facts=True,
            promised_discount_or_waiver=True,
            threatened_consequences=False,
            qa_score=0.9,
            confidence=1.0,
        )
        analysis = _run(monkeypatch, compliance=discount)
        assert analysis.exception_reason == "compliance_violation"

    def test_multiple_reasons_all_tagged(self, monkeypatch):
        # outcome type deliberately not promise_to_pay/dispute — isolates this test to just
        # low_confidence + compliance_violation, without also tripping a disagreement.
        low = OutcomeExtraction(outcome="soft_commitment", next_action="x", confidence=0.3)
        discount = ComplianceReview(
            disclosed_automated=True,
            verified_authority=True,
            stayed_within_permitted_facts=True,
            promised_discount_or_waiver=True,
            threatened_consequences=False,
            qa_score=0.9,
            confidence=1.0,
        )
        analysis = _run(monkeypatch, outcome=low, compliance=discount)
        assert analysis.exception_reason == "low_confidence,compliance_violation"


class TestHardComplianceViolations:
    """Pure code — no LLM, no monkeypatching."""

    def _base(self, **overrides) -> ComplianceReview:
        fields = dict(
            disclosed_automated=True,
            verified_authority=True,
            stayed_within_permitted_facts=True,
            promised_discount_or_waiver=False,
            threatened_consequences=False,
            misstated_total=False,
            qa_score=1.0,
            confidence=1.0,
        )
        fields.update(overrides)
        return ComplianceReview(**fields)

    def test_clean_review_has_no_hard_violations(self):
        assert pipeline._hard_compliance_violations(self._base()) == []

    def test_each_hard_violation_is_individually_detected(self):
        assert pipeline._hard_compliance_violations(self._base(promised_discount_or_waiver=True))
        assert pipeline._hard_compliance_violations(self._base(threatened_consequences=True))
        assert pipeline._hard_compliance_violations(self._base(verified_authority=False))
        assert pipeline._hard_compliance_violations(self._base(stayed_within_permitted_facts=False))
        assert pipeline._hard_compliance_violations(self._base(misstated_total=True))

    def test_disclosure_alone_is_not_currently_a_hard_violation(self):
        """disclosed_automated is intentionally not in the hard list — only the five violations
        explicitly named in the product decision are. Documented here so a future change to
        that decision changes this test, not a silent behavior drift."""
        assert pipeline._hard_compliance_violations(self._base(disclosed_automated=False)) == []
