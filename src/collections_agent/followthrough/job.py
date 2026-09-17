"""Step 6 — the daily follow-through job (design doc Stage 5).

Checks every still-`OPEN` promise whose grace period has passed against real payment data,
resolves it to `KEPT`/`PARTIAL`/`BROKEN`, and updates the account's `reliability_score`.

What this job does *not* need to do, and why (see docs/FAILURES.md for the fuller story):
  - Un-suppress the account once a promise resolves — suppression.py's open-promise rule
    already stops applying the moment `promised_date` passes, independent of `status`.
  - Escalate the account's call priority — priority.py's `_ptp_broken_escalation` already
    reads `ptp_history` directly and boosts accounts with a worse broken ratio. This job's
    only job is to make that history *true*; the scoring side was already fixed to escalate
    rather than discount broken promises (see docs/FAILURES.md).
`reliability_score` here is purely a reporting number for the Accounts tab — a human-readable
"how reliable is this account" — independent of priority.py's separate escalation multiplier.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from collections_agent.followthrough.payments import PaymentsSource
from collections_agent.models.domain import PTP, Account, Payment, PTPStatus
from collections_agent.sheets.client import SheetsBackend
from collections_agent.sheets.readers import read_accounts, read_ptps
from collections_agent.sheets.writers import seed_accounts, write_ptps

# Time after a promised_date before a still-unpaid promise counts as BROKEN rather than just
# "not resolved yet" — bank transfers can take a day or two to clear, so a promise isn't
# broken the instant its date passes. Mirrors suppression.py's DEFAULT_COOLDOWN_WORKING_DAYS
# pattern: a named, tunable constant, not a magic number buried in logic.
DEFAULT_GRACE_PERIOD_DAYS = 3

# Payment amounts and promised amounts are both floats; this absorbs paisa-level rounding so
# "paid the full amount" doesn't fail equality by a fraction of a rupee.
ROUNDING_TOLERANCE = 0.01

_RESOLVED_STATUSES = (PTPStatus.KEPT, PTPStatus.PARTIAL, PTPStatus.BROKEN)


class FollowthroughResult(BaseModel):
    resolved: list[PTP]
    accounts_updated: list[str]


def check_ptp(
    ptp: PTP,
    payments: list[Payment],
    as_of: date,
    grace_period_days: int = DEFAULT_GRACE_PERIOD_DAYS,
) -> PTP:
    """Pure function. Returns `ptp` unchanged unless it's resolvable *now*.

    Already-resolved statuses (KEPT/PARTIAL/BROKEN) and the never-set-anywhere SUPERSEDED are
    all left untouched — this only ever transitions a promise out of OPEN, once, and never
    re-judges a promise that's already been judged (re-evaluating a KEPT promise against
    payments received afterward could flip it nonsensically; this is also what makes
    re-running the job safe).

    The whole check — not just the BROKEN branch — waits until the grace period has passed.
    A promise paid in full a day early still isn't marked KEPT until the check date, so there
    is exactly one code path that decides an outcome, not "resolve early if lucky, wait it out
    otherwise" — simpler to reason about and to document (see docs/metrics.md).
    """
    if ptp.status != PTPStatus.OPEN:
        return ptp

    check_date = ptp.promised_date + timedelta(days=grace_period_days)
    if as_of < check_date:
        return ptp

    total_paid = sum(
        p.amount_paid for p in payments if p.invoice_id in ptp.invoice_ids and p.paid_date <= check_date
    )

    if total_paid >= ptp.amount_promised - ROUNDING_TOLERANCE:
        new_status = PTPStatus.KEPT
    elif total_paid > 0:
        new_status = PTPStatus.PARTIAL
    else:
        new_status = PTPStatus.BROKEN

    return ptp.model_copy(update={"status": new_status})


def _reliability_score(account_ptps: list[PTP]) -> float:
    resolved = [p for p in account_ptps if p.status in _RESOLVED_STATUSES]
    if not resolved:
        return 1.0
    kept = sum(1 for p in resolved if p.status == PTPStatus.KEPT)
    return round(kept / len(resolved), 4)


def run_followthrough(
    backend: SheetsBackend,
    payments_source: PaymentsSource,
    as_of: date,
    grace_period_days: int = DEFAULT_GRACE_PERIOD_DAYS,
) -> FollowthroughResult:
    all_ptps = read_ptps(backend)
    payments = payments_source.list_payments()

    checked = [
        check_ptp(p, payments, as_of, grace_period_days) if p.status == PTPStatus.OPEN else p
        for p in all_ptps
    ]
    resolved_this_run = [
        new for new, old in zip(checked, all_ptps, strict=True) if new.status != old.status
    ]
    if resolved_this_run:
        write_ptps(backend, resolved_this_run)

    affected_account_ids = {p.account_id for p in resolved_this_run}
    if affected_account_ids:
        accounts = {a.account_id: a for a in read_accounts(backend)}
        updated_accounts: list[Account] = []
        for account_id in affected_account_ids:
            account = accounts.get(account_id)
            if account is None:
                continue
            account_ptps = [p for p in checked if p.account_id == account_id]
            new_score = _reliability_score(account_ptps)
            if new_score != account.reliability_score:
                updated_accounts.append(account.model_copy(update={"reliability_score": new_score}))
        if updated_accounts:
            seed_accounts(backend, updated_accounts)

    return FollowthroughResult(
        resolved=resolved_this_run, accounts_updated=sorted(affected_account_ids)
    )
