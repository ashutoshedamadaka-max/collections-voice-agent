"""Priority scoring — transparent and tunable, not a black box.

priority = balance_weight x bucket_weight x ptp_broken_escalation x contactability

Every factor is a simple, inspectable function of plain data; weights live in
config/priority_weights.yaml (see rules_config.py) rather than being hard-coded here.
"""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import (
    PTP,
    Account,
    CallLogEntry,
    CallOutcomeType,
    Invoice,
    PriorityWeights,
    PTPStatus,
)


def _account_bucket(invoices: list[Invoice], as_of: date):
    unpaid = [inv for inv in invoices if inv.outstanding() > 0]
    if not unpaid:
        return None
    # the single most-overdue unpaid invoice drives the account's posture
    worst = max(unpaid, key=lambda inv: (as_of - inv.due_date).days)
    return worst.aging_bucket(as_of)


def _balance_weight(invoices: list[Invoice], weights: PriorityWeights) -> float:
    total_outstanding = sum(inv.outstanding() for inv in invoices)
    return min(total_outstanding / weights.balance_weight_denominator, weights.balance_weight_cap)


def _ptp_broken_escalation(ptp_history: list[PTP], weights: PriorityWeights) -> float:
    """A broken promise is evidence this account's commitments don't convert — that's grounds
    to call it *sooner*, not skip it. Returns a multiplier >= 1.0, scaling up with the
    account's broken-promise ratio and capped at `ptp_reliability_max`. Never < 1.0: a clean
    or promise-free history never gets a discount here, only accounts with a real broken-
    promise track record get boosted. See docs/FAILURES.md — this used to run backwards."""
    resolved = [p for p in ptp_history if p.status in (PTPStatus.KEPT, PTPStatus.BROKEN, PTPStatus.PARTIAL)]
    if not resolved:
        return 1.0
    broken_ratio = sum(1 for p in resolved if p.status == PTPStatus.BROKEN) / len(resolved)
    escalation = 1.0 + weights.ptp_broken_escalation_weight * broken_ratio
    return min(escalation, weights.ptp_reliability_max)


def _contactability(account_id: str, call_log: list[CallLogEntry], weights: PriorityWeights) -> float:
    account_calls = [c for c in call_log if c.account_id == account_id]
    if not account_calls:
        return weights.contactability_default
    last_call = max(account_calls, key=lambda c: c.called_at)
    if last_call.outcome == CallOutcomeType.WRONG_PERSON:
        return weights.contactability_wrong_person
    return weights.contactability_default


def priority_score(
    account: Account,
    invoices: list[Invoice],
    ptp_history: list[PTP],
    call_log: list[CallLogEntry],
    weights: PriorityWeights,
    as_of: date | None = None,
) -> float:
    """Return a non-negative priority score for `account`. Higher = call sooner.

    Returns 0.0 if the account has no unpaid invoices (nothing to prioritize).
    """
    as_of = as_of or date.today()
    account_invoices = [inv for inv in invoices if inv.account_id == account.account_id]
    bucket = _account_bucket(account_invoices, as_of)
    if bucket is None or bucket not in weights.bucket_weight:
        return 0.0

    account_ptps = [p for p in ptp_history if p.account_id == account.account_id]

    balance = _balance_weight(account_invoices, weights)
    bucket_w = weights.bucket_weight[bucket]
    escalation = _ptp_broken_escalation(account_ptps, weights)
    contactability = _contactability(account.account_id, call_log, weights)

    return round(balance * bucket_w * escalation * contactability, 4)
