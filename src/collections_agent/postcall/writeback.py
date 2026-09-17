"""Step 5 — writes a completed `PostCallAnalysis` back to the same Google Sheet Step 0 seeded.

Every call gets a Call_Log row, unconditionally — that's the audit trail a human needs to find
and review a call at all, including ones that got routed to the exception queue. Beyond that,
`write_decision` gates everything else: `auto_write` writes the specific PTP/SoftCommitment/
Dispute row(s) this call produced; `exception_queue` writes to Exceptions *instead of* those —
this is the design doc's confidence-gated write-back, made real rather than decorative.

Idempotency: PTP_Register/Soft_Commitments/Disputes are keyed on their own id column (not
call_id) in sheets/writers.py's `_KEY_FIELDS`, but this module derives those ids deterministically
from `call_id` (`PTP-{call_id}`, `SC-{call_id}`, `DSP-{call_id}`) rather than a random uuid —
`PostCallAnalysis` carries at most one promise and one dispute per call, so this is a safe 1:1
mapping, and it's what makes re-running write-back for the same call a true no-op upsert instead
of a duplicate row. This is a different, unrelated id space from the live in-call tool handler's
random ids (webhooks/handlers.py) — those are ephemeral, used only for the spoken confirmation
and fixtures/tool_calls.jsonl, and were never written to Sheets.

Re-analysis reconciliation (2026-09-17, see docs/FAILURES.md): a corrected re-analysis of the
same call_id can flip write_decision, promise completeness, or dispute presence between runs.
`SheetsBackend` has no delete verb, so `_reconcile_stale_rows` doesn't try to remove anything —
it marks whatever a *previous* run wrote that the *current* decision no longer confirms as
superseded (`PTPStatus.SUPERSEDED`, `SoftCommitment.superseded`, `DisputeStatus.SUPERSEDED`) or
resolved (`ExceptionEntry.resolved`), same deterministic ids, before the normal write below
runs. This was a deliberate choice over deleting rows: an audit trail (what did the *first*
analysis say, and when was it superseded) matters more in a collections system than a visually
clean sheet, and every one of these tabs is exactly the kind of record a human might need to
explain later. A stale row that's silently indistinguishable from a current one was the actual
bug — one is now unmistakable from the other.
"""

from __future__ import annotations

from datetime import UTC, datetime

from collections_agent.models.domain import (
    PTP,
    CallLogEntry,
    Dispute,
    DisputeStatus,
    ExceptionEntry,
    PostCallAnalysis,
    PTPStatus,
    RoutingTarget,
    SoftCommitment,
    WriteDecision,
)
from collections_agent.postcall.transcript import Transcript
from collections_agent.sheets.client import SheetsBackend
from collections_agent.sheets.readers import read_disputes, read_exceptions, read_ptps, read_soft_commitments
from collections_agent.sheets.writers import (
    write_call_log,
    write_disputes,
    write_exceptions,
    write_ptps,
    write_soft_commitments,
)

POSTCALL_AGENT_VERSION = "postcall-pipeline-v1"


def _supersede_stale_ptp(backend: SheetsBackend, call_id: str) -> None:
    stale = [p for p in read_ptps(backend) if p.call_id == call_id and p.status != PTPStatus.SUPERSEDED]
    if stale:
        write_ptps(backend, [p.model_copy(update={"status": PTPStatus.SUPERSEDED}) for p in stale])


def _supersede_stale_soft_commitment(backend: SheetsBackend, call_id: str) -> None:
    stale = [s for s in read_soft_commitments(backend) if s.call_id == call_id and not s.superseded]
    if stale:
        write_soft_commitments(backend, [s.model_copy(update={"superseded": True}) for s in stale])


def _supersede_stale_dispute(backend: SheetsBackend, call_id: str) -> None:
    all_disputes = read_disputes(backend)
    stale = [d for d in all_disputes if d.call_id == call_id and d.status != DisputeStatus.SUPERSEDED]
    if stale:
        write_disputes(backend, [d.model_copy(update={"status": DisputeStatus.SUPERSEDED}) for d in stale])


def _resolve_stale_exception(backend: SheetsBackend, call_id: str) -> None:
    stale = [e for e in read_exceptions(backend) if e.call_id == call_id and not e.resolved]
    if not stale:
        return
    resolved_note = f"resolved_by_reanalysis ({datetime.now(UTC).isoformat()})"
    write_exceptions(backend, [e.model_copy(update={"resolved": resolved_note}) for e in stale])


def _reconcile_stale_rows(backend: SheetsBackend, analysis: PostCallAnalysis) -> None:
    """Marks whatever a previous write_back run for this call_id left behind that the current
    decision no longer confirms — see module docstring. Never touches a row a human already
    resolved by hand (a non-empty `resolved`/non-OPEN `status` is left exactly as it is).
    """
    promise = analysis.promise
    wants_ptp = (
        analysis.write_decision == WriteDecision.AUTO_WRITE
        and promise.has_promise
        and not promise.downgraded_to_soft_commitment
    )
    wants_soft_commitment = (
        analysis.write_decision == WriteDecision.AUTO_WRITE
        and promise.has_promise
        and promise.downgraded_to_soft_commitment
    )
    wants_dispute = analysis.write_decision == WriteDecision.AUTO_WRITE and analysis.dispute.has_dispute
    wants_exception = analysis.write_decision == WriteDecision.EXCEPTION_QUEUE

    if not wants_ptp:
        _supersede_stale_ptp(backend, analysis.call_id)
    if not wants_soft_commitment:
        _supersede_stale_soft_commitment(backend, analysis.call_id)
    if not wants_dispute:
        _supersede_stale_dispute(backend, analysis.call_id)
    if not wants_exception:
        _resolve_stale_exception(backend, analysis.call_id)


def write_back(backend: SheetsBackend, analysis: PostCallAnalysis, transcript: Transcript) -> None:
    if transcript.started_at is None:
        raise ValueError(
            f"call {analysis.call_id} has no started_at — re-run `pull-transcripts "
            f"{analysis.call_id}` to backfill it before writing back (see docs/FAILURES.md: "
            "this codebase rejects missing data rather than fabricating a timestamp)."
        )

    _reconcile_stale_rows(backend, analysis)

    call_log_entry = CallLogEntry(
        call_id=analysis.call_id,
        account_id=analysis.account_id,
        called_at=transcript.started_at,
        duration_seconds=round(transcript.duration_seconds or 0),
        outcome=analysis.outcome.outcome,
        reason_code=analysis.outcome.reason_code,
        cost_usd=transcript.cost_usd,
        # Not transcript.recording_url — Vapi's recording links are presigned and expire in
        # ~30 minutes, so anything stored here would already be dead by the time a human reads
        # this row. Point at the command that mints a fresh one instead. See docs/FAILURES.md.
        recording_url=f"fetch-recording {analysis.call_id}",
        qa_score=analysis.compliance.qa_score,
        summary=analysis.summary,
    )
    write_call_log(backend, [call_log_entry])

    if analysis.write_decision == WriteDecision.EXCEPTION_QUEUE:
        write_exceptions(
            backend,
            [
                ExceptionEntry(
                    call_id=analysis.call_id,
                    account_id=analysis.account_id,
                    reason=analysis.exception_reason,
                    supervisor_notes=analysis.supervisor_notes,
                    created_at=datetime.now(UTC),
                )
            ],
        )
        return

    promise = analysis.promise
    if promise.has_promise and not promise.downgraded_to_soft_commitment:
        write_ptps(
            backend,
            [
                PTP(
                    ptp_id=f"PTP-{analysis.call_id}",
                    account_id=analysis.account_id,
                    invoice_ids=promise.invoice_ids,
                    amount_promised=promise.amount or 0.0,
                    promised_date=promise.promised_date,
                    payment_method=promise.method,
                    captured_at=transcript.started_at,
                    captured_by=POSTCALL_AGENT_VERSION,
                    confidence=promise.confidence,
                    status=PTPStatus.OPEN,
                    call_id=analysis.call_id,
                )
            ],
        )
    elif promise.has_promise and promise.downgraded_to_soft_commitment:
        write_soft_commitments(
            backend,
            [
                SoftCommitment(
                    soft_commitment_id=f"SC-{analysis.call_id}",
                    account_id=analysis.account_id,
                    invoice_ids=promise.invoice_ids,
                    note=promise.notes,
                    captured_at=transcript.started_at,
                    captured_by=POSTCALL_AGENT_VERSION,
                    call_id=analysis.call_id,
                )
            ],
        )

    dispute = analysis.dispute
    if dispute.has_dispute:
        if dispute.reason_code is None:
            # Unlike routing_target (safe generic fallback: ops), there's no sensible generic
            # ReasonCode — fail loudly rather than write a fabricated/misleading reason a human
            # reviewer would trust, consistent with this codebase's "reject bad data, don't
            # fabricate" convention (handlers.py's date validation, compliance.py's
            # "unverifiable, not a violation").
            raise ValueError(
                f"call {analysis.call_id}: dispute classifier set has_dispute=True with no "
                "reason_code — inconsistent specialist output, needs manual review before "
                "this can write to Disputes."
            )
        write_disputes(
            backend,
            [
                Dispute(
                    dispute_id=f"DSP-{analysis.call_id}",
                    account_id=analysis.account_id,
                    invoice_id=dispute.invoice_id or "",
                    reason_code=dispute.reason_code,
                    detail=dispute.detail,
                    evidence_requested=dispute.evidence_requested,
                    # Fallback for the rare case the classifier set has_dispute=True without a
                    # routing_target — don't crash an otherwise-clean auto-write over one
                    # missing enum; ops is the safest generic landing spot for human triage.
                    routing_target=dispute.routing_target or RoutingTarget.OPS,
                    status=DisputeStatus.OPEN,
                    opened_at=transcript.started_at,
                    call_id=analysis.call_id,
                )
            ],
        )
