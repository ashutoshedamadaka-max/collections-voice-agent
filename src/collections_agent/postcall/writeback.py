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

Known limitation: SheetsBackend has no delete verb. If a call's classification changes across
re-runs (e.g. a re-analysis flips promise -> soft commitment), the stale row in the other tab
isn't cleaned up automatically. Acceptable today since that only happens on manual re-analysis
— noted here so it isn't mistaken for a bug later.
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
from collections_agent.sheets.writers import (
    write_call_log,
    write_disputes,
    write_exceptions,
    write_ptps,
    write_soft_commitments,
)

POSTCALL_AGENT_VERSION = "postcall-pipeline-v1"


def write_back(backend: SheetsBackend, analysis: PostCallAnalysis, transcript: Transcript) -> None:
    if transcript.started_at is None:
        raise ValueError(
            f"call {analysis.call_id} has no started_at — re-run `pull-transcripts "
            f"{analysis.call_id}` to backfill it before writing back (see docs/FAILURES.md: "
            "this codebase rejects missing data rather than fabricating a timestamp)."
        )

    call_log_entry = CallLogEntry(
        call_id=analysis.call_id,
        account_id=analysis.account_id,
        called_at=transcript.started_at,
        duration_seconds=round(transcript.duration_seconds or 0),
        outcome=analysis.outcome.outcome,
        reason_code=analysis.outcome.reason_code,
        cost_usd=transcript.cost_usd,
        recording_url=transcript.recording_url,
        qa_score=analysis.compliance.qa_score,
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
