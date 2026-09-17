"""The suppression engine — decides which accounts we should NOT call today, and why.

Every rule here is pure: it takes plain in-memory data and `now`, and returns a
SuppressionResult with every reason that applied (never just the first one), so the
Suppressed-tab audit trail can show the full picture. "Calls we correctly did not make" is
itself a product metric, not a side effect.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from collections_agent.models.domain import (
    PTP,
    Account,
    AgingBucket,
    CallLogEntry,
    Invoice,
    PTPStatus,
    SuppressionResult,
)

DEFAULT_COOLDOWN_WORKING_DAYS = 5
CALLING_HOURS_START = time(9, 0)
CALLING_HOURS_END = time(18, 0)


def _working_days_between(start: datetime, end: datetime) -> int:
    """Count weekday-only days between two datetimes, ignoring partial days."""
    if end <= start:
        return 0
    days = 0
    cursor = start.date()
    end_date = end.date()
    while cursor < end_date:
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:  # Mon-Fri
            days += 1
    return days


def _within_cooldown(
    account_id: str,
    call_log: list[CallLogEntry],
    now: datetime,
    cooldown_working_days: int,
) -> bool:
    account_calls = [c for c in call_log if c.account_id == account_id]
    if not account_calls:
        return False
    last_call = max(account_calls, key=lambda c: c.called_at)
    return _working_days_between(last_call.called_at, now) < cooldown_working_days


def _outside_calling_hours(account: Account, now: datetime) -> bool:
    local_now = now.astimezone(ZoneInfo(account.timezone))
    if local_now.weekday() >= 5:  # weekend
        return True
    return not (CALLING_HOURS_START <= local_now.time() <= CALLING_HOURS_END)


def is_suppressed(
    account: Account,
    invoices: list[Invoice],
    call_log: list[CallLogEntry],
    open_ptps: list[PTP],
    now: datetime,
    cooldown_working_days: int = DEFAULT_COOLDOWN_WORKING_DAYS,
) -> SuppressionResult:
    """Decide whether `account` should be suppressed from today's call queue.

    `invoices` should be every invoice for this account (any status); `open_ptps` should be
    every PTP for this account regardless of status (filtered here).
    """
    reasons: list[str] = []

    account_invoices = [inv for inv in invoices if inv.account_id == account.account_id]
    unpaid_invoices = [inv for inv in account_invoices if inv.outstanding() > 0]
    if account_invoices and not unpaid_invoices:
        reasons.append("invoice paid or partially paid since last sync")

    account_open_ptps = [
        p for p in open_ptps if p.account_id == account.account_id and p.status == PTPStatus.OPEN
    ]
    if any(p.promised_date >= now.date() for p in account_open_ptps):
        reasons.append("an open promise exists and its date hasn't passed yet")

    if account.opted_out:
        reasons.append("contact opted out")
    if account.wrong_party:
        reasons.append("contact marked wrong-party")

    if _within_cooldown(account.account_id, call_log, now, cooldown_working_days):
        reasons.append(f"called within the last {cooldown_working_days} working days")

    if _outside_calling_hours(account, now):
        reasons.append("outside calling hours for the account's time zone")

    as_of = now.date()
    if any(inv.aging_bucket(as_of) == AgingBucket.D90_PLUS for inv in unpaid_invoices):
        reasons.append("bucket is 90+, route to a human")

    if account.in_active_payment_plan and account.payment_plan_on_schedule:
        reasons.append("account is in an active payment plan and on schedule")

    return SuppressionResult(suppressed=bool(reasons), reasons=reasons)
