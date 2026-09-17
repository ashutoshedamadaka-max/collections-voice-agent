"""Step 6 — the Metrics tab rollup (design doc Stage 5/build-order row 6).

Full definitions, and why each formula is shaped the way it is, live in docs/metrics.md — this
module is the implementation of that document, not a second source of truth for the reasoning.
"""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import (
    PTP,
    CallLogEntry,
    CallOutcomeType,
    Dispute,
    ExceptionEntry,
    MetricsRow,
    PTPStatus,
)

# Nothing writes to the Suppressed tab yet — that needs `build-queue` (a daily dispatch job
# that doesn't exist yet; see README's "later commands" list). Hardcoded rather than computed
# from a tab with no writer, so this is an honest, documented gap, not a silently wrong number.
_CALLS_SUPPRESSED_UNAVAILABLE = 0

_NOT_CONTACT = {CallOutcomeType.NO_ANSWER}
_NOT_STRUCTURED = {CallOutcomeType.NO_ANSWER, CallOutcomeType.HOSTILE, CallOutcomeType.WRONG_PERSON}
_KEPT_RATE_DENOMINATOR_STATUSES = (PTPStatus.KEPT, PTPStatus.PARTIAL, PTPStatus.BROKEN)


def _safe_rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def compute_metrics(
    call_log: list[CallLogEntry],
    ptps: list[PTP],
    disputes: list[Dispute],
    exceptions: list[ExceptionEntry],
    target_date: date,
) -> MetricsRow:
    day_calls = [c for c in call_log if c.called_at.date() == target_date]
    calls_made = len(day_calls)

    contact = sum(1 for c in day_calls if c.outcome not in _NOT_CONTACT)
    structured = sum(1 for c in day_calls if c.outcome not in _NOT_STRUCTURED)
    day_exceptions = sum(1 for e in exceptions if e.created_at.date() == target_date)

    # Cumulative, not a daily cohort or trailing window — see docs/metrics.md for why.
    # PARTIAL counts in the denominator (it's a resolved promise) but never the numerator
    # (it isn't a kept one) — a partial payment is not a kept promise.
    resolved = [p for p in ptps if p.status in _KEPT_RATE_DENOMINATOR_STATUSES]
    kept = [p for p in resolved if p.status == PTPStatus.KEPT]
    kept_rate = _safe_rate(len(kept), len(resolved))
    kept_rate_by_value = _safe_rate(
        sum(p.amount_promised for p in kept), sum(p.amount_promised for p in resolved)
    )

    return MetricsRow(
        date=target_date,
        calls_made=calls_made,
        calls_suppressed=_CALLS_SUPPRESSED_UNAVAILABLE,
        contact_rate=_safe_rate(contact, calls_made),
        structured_outcome_rate=_safe_rate(structured, calls_made),
        promise_to_pay_kept_rate=kept_rate,
        promise_to_pay_kept_rate_by_value=kept_rate_by_value,
        disputes_surfaced=sum(1 for d in disputes if d.opened_at.date() == target_date),
        human_review_rate=_safe_rate(day_exceptions, calls_made),
    )
