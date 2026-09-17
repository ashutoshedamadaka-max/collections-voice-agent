"""Runs the four post-call specialists and merges their outputs (design doc Stage 3).

Specialists are independent, network-bound LLM calls, so they run in parallel via a thread
pool rather than sequentially — the design doc's diagram draws them side by side for exactly
this reason. The supervisor routes a call to the human exception queue, instead of letting it
auto-write (Step 5), for three independent reasons: below-threshold confidence from any
*material* specialist, a specific set of cross-specialist disagreements, or a hard compliance
violation (see _hard_compliance_violations) — the last of these overrides confidence entirely
by design, not by omission. This is what makes the human-in-the-loop story honest rather than
decorative.

"Material" matters because plain min()-across-all-four is too blunt: a confident "no dispute"
on a clean call would otherwise drag every call through the same gate as a genuine dispute a
specialist is unsure about. Promise/dispute confidence only counts when that specialist
actually found something relevant to write back; outcome and compliance always count, since
every call gets a Call_Log row and a QA score regardless of what else happened.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from collections_agent.models.domain import (
    CallOutcomeType,
    ComplianceReview,
    DisputeClassification,
    Invoice,
    OutcomeExtraction,
    PostCallAnalysis,
    PromiseValidation,
    WriteDecision,
)
from collections_agent.postcall.specialists.compliance import review_compliance
from collections_agent.postcall.specialists.dispute import classify_dispute
from collections_agent.postcall.specialists.outcome import extract_outcome
from collections_agent.postcall.specialists.promise import validate_promise
from collections_agent.postcall.transcript import Transcript


def _disagreements(
    outcome: OutcomeExtraction, promise: PromiseValidation, dispute: DisputeClassification
) -> list[str]:
    """Cases where two specialists can't both be right — each is independently plausible-looking
    but only cross-checking catches it, which is the whole point of a supervisor instead of
    trusting one specialist's confidence score in isolation."""
    reasons = []
    if outcome.outcome == CallOutcomeType.PROMISE_TO_PAY and not promise.has_promise:
        reasons.append("outcome says promise_to_pay but the promise specialist found no promise")
    if outcome.outcome == CallOutcomeType.PROMISE_TO_PAY and promise.downgraded_to_soft_commitment:
        reasons.append("outcome says promise_to_pay but the promise was downgraded to a soft commitment")
    if outcome.outcome == CallOutcomeType.DISPUTE and not dispute.has_dispute:
        reasons.append("outcome says dispute but the dispute specialist found none")
    return reasons


def _hard_compliance_violations(compliance: ComplianceReview) -> list[str]:
    """Product decision, not an implementation detail: some compliance findings are severe
    enough to force human review regardless of how confident every specialist felt, and some
    aren't. A hard violation is something the caller could act on or be harmed by if it goes
    out uncorrected — a promised discount that wasn't authorized, a threat that was never
    approved, amounts stated without confirming who was on the line, a fact fabricated beyond
    what the agent was given, or a number that doesn't add up. Those always route to the
    exception queue; nothing here overrides that with a confidence score. Soft findings — tone,
    register, minor phrasing — are recorded on the call log via qa_score/notes but don't block
    the write; routing every stylistic nitpick to a human defeats the point of automation.
    """
    violations = []
    if compliance.promised_discount_or_waiver:
        violations.append("agent promised a discount or waiver")
    if compliance.threatened_consequences:
        violations.append("agent threatened consequences")
    if not compliance.verified_authority:
        violations.append("agent did not verify the caller's authority before discussing amounts")
    if not compliance.stayed_within_permitted_facts:
        violations.append("agent stated a fact not given to it as context")
    if compliance.misstated_total:
        violations.append("agent misstated a total or amount")
    return violations


def _material_confidences(
    outcome: OutcomeExtraction,
    promise: PromiseValidation,
    dispute: DisputeClassification,
    compliance: ComplianceReview,
) -> list[float]:
    """Only a specialist whose finding is actually material to this call's write-back should
    gate it. outcome and compliance are always material — every call gets a Call_Log row and a
    QA score. promise/dispute are material only if they found something to write."""
    confidences = [outcome.confidence, compliance.confidence]
    if promise.has_promise:
        confidences.append(promise.confidence)
    if dispute.has_dispute:
        confidences.append(dispute.confidence)
    return confidences


def run_postcall(
    call_id: str,
    transcript: Transcript,
    invoices: list[Invoice],
    as_of: date,
    api_key: str,
    confidence_threshold: float,
) -> PostCallAnalysis:
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcome_future = pool.submit(extract_outcome, transcript, api_key)
        promise_future = pool.submit(validate_promise, transcript, invoices, as_of, api_key)
        dispute_future = pool.submit(classify_dispute, transcript, api_key)
        compliance_future = pool.submit(review_compliance, transcript, invoices, api_key)

        outcome = outcome_future.result()
        promise = promise_future.result()
        dispute = dispute_future.result()
        compliance = compliance_future.result()

    overall_confidence = min(_material_confidences(outcome, promise, dispute, compliance))
    disagreements = _disagreements(outcome, promise, dispute)
    hard_violations = _hard_compliance_violations(compliance)

    if overall_confidence < confidence_threshold or disagreements or hard_violations:
        write_decision = WriteDecision.EXCEPTION_QUEUE
        notes = []
        if overall_confidence < confidence_threshold:
            notes.append(
                f"overall confidence {overall_confidence:.2f} below threshold {confidence_threshold:.2f}"
            )
        notes.extend(disagreements)
        notes.extend(hard_violations)
        supervisor_notes = "; ".join(notes)
    else:
        write_decision = WriteDecision.AUTO_WRITE
        supervisor_notes = ""

    return PostCallAnalysis(
        call_id=call_id,
        outcome=outcome,
        promise=promise,
        dispute=dispute,
        compliance=compliance,
        overall_confidence=overall_confidence,
        write_decision=write_decision,
        supervisor_notes=supervisor_notes,
    )
