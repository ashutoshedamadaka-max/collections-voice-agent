"""Renders the per-call system prompt from a ContextPack.

All fact-assembly happens here in tested Python, not in Vapi's own templating — the rendered
string is passed as a per-call assistantOverrides system-prompt override (see vapi_client.py).
This is also the single place that decides exactly which numbers, dates, and references the
agent is allowed to say; `allowed_facts()` exposes that same set so tests can assert the
prompt never contains anything outside it.

Structural fix, 2026-09-29 (see docs/FAILURES.md): earlier, every fact appeared twice — a raw
form ("204,000.00") and a `(say "...")` spoken form side by side — on the theory that the model
would use the spoken one. Live calls (Hindi and English, gpt-4o-mini and gpt-4o) kept reading
the raw form instead, invoice numbers and amounts alike. The `# YOUR ONLY FACTS` section below
now contains *only* spoken forms — there is nothing else there to fall back to reading. The
small number of raw values genuinely needed as tool-call arguments (an invoice's identifier,
and its outstanding amount for the "did they agree to pay in full" case) live in a separate
`# REFERENCE VALUES` section, explicitly labeled as never to be spoken.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from collections_agent.models.domain import ContextPack, Invoice
from collections_agent.voice.language import prompt_instruction
from collections_agent.voice.speakable import amount_to_words, date_to_words, invoice_number_to_words

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=()),  # plain text prompt, not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)


def _invoice_spoken(inv: Invoice) -> str:
    """What the model may actually say about this invoice — no raw digits, no raw id."""
    return (
        f'invoice number "{invoice_number_to_words(inv.invoice_number)}", due '
        f'"{date_to_words(inv.due_date)}", outstanding "{amount_to_words(inv.outstanding())}"'
    )


def _invoice_reference(inv: Invoice) -> str:
    """Raw values for tool-call arguments only. No due date here — no tool call ever takes an
    invoice's due date as an argument, only its identifier and (for a "pay in full" promise)
    its outstanding amount."""
    return (
        f'{inv.invoice_number} — if they agree to pay the full outstanding amount, that amount '
        f'is {inv.outstanding():,.2f}'
    )


def _primary_invoice(context_pack: ContextPack) -> Invoice:
    return next(inv for inv in context_pack.invoices if inv.invoice_id == context_pack.primary_invoice_id)


def _other_invoices(context_pack: ContextPack) -> list[Invoice]:
    return [inv for inv in context_pack.invoices if inv.invoice_id != context_pack.primary_invoice_id]


def _format_primary_invoice_spoken(context_pack: ContextPack) -> str:
    return _invoice_spoken(_primary_invoice(context_pack))


def _format_other_invoices_spoken(context_pack: ContextPack) -> str:
    """Empty string (not "none") when there are none — the template's {% if %} on this value
    omits the whole "other invoices" section rather than printing a "none" line for the common
    single-invoice case."""
    return "; ".join(_invoice_spoken(inv) for inv in _other_invoices(context_pack))


def _format_invoice_references(context_pack: ContextPack) -> str:
    """The primary invoice, then any others — every invoice this call could plausibly need a
    raw identifier for, in one place, clearly separated from anything spoken."""
    invoices = [_primary_invoice(context_pack), *_other_invoices(context_pack)]
    return "; ".join(_invoice_reference(inv) for inv in invoices)


def _format_ptp_history(context_pack: ContextPack) -> str:
    if not context_pack.prior_promises:
        return "none"
    parts = [
        f'"{date_to_words(p.promised_date)}" for "{amount_to_words(p.amount_promised)}" '
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
        primary_invoice_spoken=_format_primary_invoice_spoken(context_pack),
        other_invoices_spoken=_format_other_invoices_spoken(context_pack),
        invoice_references=_format_invoice_references(context_pack),
        total_outstanding_spoken=f'"{amount_to_words(context_pack.total_outstanding)}"',
        terms=context_pack.payment_terms,
        ptp_history=_format_ptp_history(context_pack),
        open_disputes=_format_open_disputes(context_pack),
        language_instruction=prompt_instruction(context_pack.preferred_language),
    )


def allowed_facts(context_pack: ContextPack) -> set[str]:
    """Every invoice number, amount, and date the agent is allowed to speak or reference.

    Used by tests to assert a rendered prompt (or, later, a transcript) never states a fact
    that isn't in the context pack. Includes both the spoken forms (what should appear in
    `# YOUR ONLY FACTS`) and the raw forms that legitimately appear in `# REFERENCE VALUES` —
    an invoice's own due date is deliberately excluded from the raw set, since no tool call
    ever takes it as an argument and it no longer appears in the prompt in raw form at all.
    """
    facts: set[str] = set()
    for inv in context_pack.invoices:
        facts.add(inv.invoice_number)
        facts.add(invoice_number_to_words(inv.invoice_number))
        facts.add(date_to_words(inv.due_date))
        facts.add(f"{inv.outstanding():,.2f}")
        facts.add(amount_to_words(inv.outstanding()))
    facts.add(amount_to_words(context_pack.total_outstanding))
    for p in context_pack.prior_promises:
        facts.add(date_to_words(p.promised_date))
        facts.add(amount_to_words(p.amount_promised))
    return facts
