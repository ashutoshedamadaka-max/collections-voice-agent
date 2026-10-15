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

import hashlib
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
    attribution_warnings: list[str] = []


def payment_key(payment: Payment) -> str:
    """Prefer a bank/import ID; identical legacy rows conservatively count once."""
    identity = (payment.invoice_id, payment.payment_id or payment.reference)
    if not identity[1]:
        identity = (
            payment.invoice_id,
            payment.paid_date.isoformat(),
            payment.method.value,
            payment.amount_paid,
        )
    return hashlib.sha256(repr(identity).encode()).hexdigest()


def _ledger(payments: list[Payment]) -> dict[str, Payment]:
    ledger: dict[str, Payment] = {}
    for payment in payments:
        key = payment_key(payment)
        if key in ledger and ledger[key] != payment:
            raise ValueError("Conflicting payment rows for the same transaction identity")
        ledger[key] = payment
    return ledger


def _eligible(ptp: PTP, payment: Payment, grace_period_days: int) -> bool:
    # Date-only bank entries on the capture day cannot prove payment followed the call.
    return (
        payment.invoice_id in ptp.invoice_ids
        and ptp.captured_at.date()
        < payment.paid_date
        <= ptp.promised_date + timedelta(days=grace_period_days)
    )


def check_ptp(
    ptp: PTP,
    payments: list[Payment],
    as_of: date,
    grace_period_days: int = DEFAULT_GRACE_PERIOD_DAYS,
    available: dict[str, float] | None = None,
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

    ledger = _ledger(payments)
    remaining = available if available is not None else {k: p.amount_paid for k, p in ledger.items()}
    allocations: dict[str, float] = {}
    need = ptp.amount_promised
    for key, payment in sorted(ledger.items(), key=lambda item: (item[1].paid_date, item[0])):
        if not _eligible(ptp, payment, grace_period_days):
            continue
        amount = min(need, remaining.get(key, 0))
        if amount > 0:
            allocations[key] = amount
            remaining[key] -= amount
            need -= amount
    total_paid = sum(allocations.values())

    if total_paid >= ptp.amount_promised - ROUNDING_TOLERANCE:
        new_status = PTPStatus.KEPT
    elif total_paid > 0:
        new_status = PTPStatus.PARTIAL
    else:
        new_status = PTPStatus.BROKEN

    return ptp.model_copy(
        update={"status": new_status, "payment_allocations": allocations, "resolved_on": as_of}
    )


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

    ledger = _ledger(payments)
    available = {k: p.amount_paid for k, p in ledger.items()}
    warnings: list[str] = []
    for ptp in all_ptps:
        for key, amount in ptp.payment_allocations.items():
            if amount < 0 or amount > available.get(key, 0) + ROUNDING_TOLERANCE:
                raise ValueError(f"Ledger changed or overallocated: {ptp.ptp_id}; reconcile before running")
            available[key] = max(0, available[key] - amount)
    legacy_reserved: set[str] = set()
    for ptp in all_ptps:
        if ptp.status in (PTPStatus.KEPT, PTPStatus.PARTIAL) and ptp.resolved_on is None:
            warnings.append(f"{ptp.ptp_id}: legacy attribution unknown; matching funds reserved")
            for key, payment in ledger.items():
                if _eligible(ptp, payment, grace_period_days):
                    available[key] = 0
                    legacy_reserved.add(key)
    by_id = {}
    for ptp in sorted(all_ptps, key=lambda p: (p.promised_date, p.captured_at, p.ptp_id)):
        if ptp.status == PTPStatus.OPEN and any(
            _eligible(ptp, ledger[key], grace_period_days) for key in legacy_reserved
        ):
            warnings.append(
                f"{ptp.ptp_id}: attribution blocked by legacy promise; left open for reconciliation"
            )
            by_id[ptp.ptp_id] = ptp
            continue
        by_id[ptp.ptp_id] = check_ptp(ptp, payments, as_of, grace_period_days, available)
    checked = [by_id[p.ptp_id] for p in all_ptps]
    resolved_this_run = [new for new, old in zip(checked, all_ptps, strict=True) if new.status != old.status]
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
        resolved=resolved_this_run,
        accounts_updated=sorted(affected_account_ids),
        attribution_warnings=warnings,
    )
