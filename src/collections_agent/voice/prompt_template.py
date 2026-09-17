"""Renders the per-call system prompt from a ContextPack.

All fact-assembly happens here in tested Python, not in Vapi's own templating — the rendered
string is passed as a per-call assistantOverrides system-prompt override (see vapi_client.py).
This is also the single place that decides exactly which numbers, dates, and references the
agent is allowed to say; `allowed_facts()` exposes that same set so tests can assert the
prompt never contains anything outside it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from collections_agent.models.domain import ContextPack

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=()),  # plain text prompt, not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)


def _format_invoice_table(context_pack: ContextPack) -> str:
    rows = [
        f"{inv.invoice_number} (due {inv.due_date.isoformat()}, outstanding {inv.outstanding():,.2f})"
        for inv in context_pack.invoices
    ]
    return "; ".join(rows) if rows else "none"


def _format_ptp_history(context_pack: ContextPack) -> str:
    if not context_pack.prior_promises:
        return "none"
    parts = [
        f"{p.promised_date.isoformat()} for {p.amount_promised:,.2f} "
        f"via {p.payment_method.value} ({p.status.value})"
        for p in context_pack.prior_promises
    ]
    return "; ".join(parts)


def _format_open_disputes(context_pack: ContextPack) -> str:
    if not context_pack.open_disputes:
        return "none"
    parts = [
        f"{d.reason_code.value} on invoice {d.invoice_id}: {d.detail}" for d in context_pack.open_disputes
    ]
    return "; ".join(parts)


def render_system_prompt(
    context_pack: ContextPack, company_name: str, current_date: date | None = None
) -> str:
    current_date = current_date or date.today()
    template = _env.get_template("system_prompt.j2")
    return template.render(
        company_name=company_name,
        current_date=current_date.isoformat(),
        contact_name=context_pack.contact_name,
        contact_role=context_pack.contact_role,
        customer_name=context_pack.customer_name,
        invoice_table=_format_invoice_table(context_pack),
        total_outstanding=f"{context_pack.total_outstanding:,.2f}",
        terms=context_pack.payment_terms,
        ptp_history=_format_ptp_history(context_pack),
        open_disputes=_format_open_disputes(context_pack),
    )


def allowed_facts(context_pack: ContextPack) -> set[str]:
    """Every invoice number, amount, and date the agent is allowed to speak.

    Used by tests to assert a rendered prompt (or, later, a transcript) never states a fact
    that isn't in the context pack.
    """
    facts: set[str] = set()
    for inv in context_pack.invoices:
        facts.add(inv.invoice_number)
        facts.add(inv.due_date.isoformat())
        facts.add(f"{inv.outstanding():,.2f}")
    facts.add(f"{context_pack.total_outstanding:,.2f}")
    for p in context_pack.prior_promises:
        facts.add(p.promised_date.isoformat())
        facts.add(f"{p.amount_promised:,.2f}")
    return facts
