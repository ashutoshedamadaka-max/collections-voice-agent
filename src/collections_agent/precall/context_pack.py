"""Assembles the ContextPack handed to the voice agent for one call.

This is the single place that decides what facts the agent is allowed to know and therefore
allowed to say — the system prompt template (voice/prompt_template.py) only ever renders
what's in this object. Pure function: no Sheets/Vapi/OpenAI calls here.
"""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import (
    PTP,
    Account,
    CallLogEntry,
    ContextPack,
    Dispute,
    DisputeStatus,
    Invoice,
)


def _account_bucket(invoices: list[Invoice], as_of: date):
    unpaid = [inv for inv in invoices if inv.outstanding() > 0]
    if not unpaid:
        return unpaid  # empty -> caller handles
    worst = max(unpaid, key=lambda inv: (as_of - inv.due_date).days)
    return worst.aging_bucket(as_of)


def build_context_pack(
    account: Account,
    invoices: list[Invoice],
    ptp_history: list[PTP],
    open_disputes: list[Dispute],
    call_log: list[CallLogEntry],
    as_of: date | None = None,
) -> ContextPack:
    as_of = as_of or date.today()

    account_invoices = [inv for inv in invoices if inv.account_id == account.account_id]
    unpaid_invoices = [inv for inv in account_invoices if inv.outstanding() > 0]
    bucket = _account_bucket(account_invoices, as_of)
    if not unpaid_invoices or bucket is None:
        raise ValueError(f"account {account.account_id} has no unpaid invoices — nothing to call about")

    account_ptps = sorted(
        (p for p in ptp_history if p.account_id == account.account_id),
        key=lambda p: p.captured_at,
        reverse=True,
    )
    account_open_disputes = [
        d for d in open_disputes if d.account_id == account.account_id and d.status != DisputeStatus.RESOLVED
    ]

    account_calls = sorted(
        (c for c in call_log if c.account_id == account.account_id),
        key=lambda c: c.called_at,
        reverse=True,
    )
    last_call = account_calls[0] if account_calls else None

    return ContextPack(
        account_id=account.account_id,
        contact_name=account.contact_name,
        contact_role=account.contact_role,
        customer_name=account.customer_name,
        invoices=unpaid_invoices,
        total_outstanding=round(sum(inv.outstanding() for inv in unpaid_invoices), 2),
        payment_terms=account.payment_terms,
        preferred_language=account.preferred_language,
        prior_promises=account_ptps,
        open_disputes=account_open_disputes,
        last_contact_date=last_call.called_at if last_call else None,
        last_contact_outcome=last_call.outcome if last_call else None,
        bucket=bucket,
    )
