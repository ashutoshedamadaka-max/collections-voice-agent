"""Step 6 — the Metrics tab rollup (design doc Stage 5/build-order row 6).

Full definitions, and why each formula is shaped the way it is, live in docs/metrics.md — this
module is the implementation of that document, not a second source of truth for the reasoning.
"""

from __future__ import annotations

from datetime import date, timedelta

from collections_agent.followthrough.job import DEFAULT_GRACE_PERIOD_DAYS
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
_CALLS_SUPPRESSED_UNAVAILABLE = None

_NOT_CONTACT = {CallOutcomeType.NO_ANSWER}
_NOT_STRUCTURED = {CallOutcomeType.NO_ANSWER, CallOutcomeType.HOSTILE, CallOutcomeType.WRONG_PERSON}
_KEPT_RATE_DENOMINATOR_STATUSES = (PTPStatus.KEPT, PTPStatus.PARTIAL, PTPStatus.BROKEN)


def _safe_rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


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
    day_call_ids = {c.call_id for c in day_calls}
    # A late review belongs to the original call cohort, and retries count once.
    day_exceptions = len({e.call_id for e in exceptions if e.call_id in day_call_ids})

    # Cumulative, not a daily cohort or trailing window — see docs/metrics.md for why.
    # PARTIAL counts in the denominator (it's a resolved promise) but never the numerator
    # (it isn't a kept one) — a partial payment is not a kept promise.
    mature = [
        p
        for p in ptps
        if p.promised_date + timedelta(days=DEFAULT_GRACE_PERIOD_DAYS) <= target_date
        and p.captured_at.date() <= target_date
    ]
    resolved = [
        p
        for p in mature
        if p.status in _KEPT_RATE_DENOMINATOR_STATUSES
        and p.resolved_on is not None
        and p.resolved_on <= target_date
    ]
    kept = [p for p in resolved if p.status == PTPStatus.KEPT]
    kept_rate = _safe_rate(len(kept), len(resolved))
    kept_rate_by_value = _safe_rate(
        sum(p.amount_promised for p in kept), sum(p.amount_promised for p in resolved)
    )
    promised_value = sum(p.amount_promised for p in resolved)
    # Never infer cash received from a legacy KEPT status.
    attributed = (
        sum(sum(p.payment_allocations.values()) for p in resolved)
        if resolved and all(p.resolved_on is not None for p in resolved)
        else None
    )
    call_cost = (
        sum(c.cost_usd for c in day_calls)
        if day_calls and all(c.cost_usd is not None for c in day_calls)
        else None
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
        contacted_calls=contact,
        business_outcome_calls=structured,
        record_review_calls=day_exceptions,
        resolved_promises=len(resolved),
        kept_promises=len(kept),
        matured_unresolved_promises=sum(p.status == PTPStatus.OPEN for p in mature),
        legacy_unverified_promises=sum(
            p.status in _KEPT_RATE_DENOMINATOR_STATUSES and p.resolved_on is None for p in mature
        ),
        resolved_promised_value=promised_value,
        attributed_payment_value=attributed,
        attributed_recovery_rate=_safe_rate(attributed, promised_value) if attributed is not None else None,
        call_cost_usd=call_cost,
        cost_per_contact_usd=_safe_rate(call_cost, contact) if call_cost is not None else None,
    )
