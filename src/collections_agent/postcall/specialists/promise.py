"""Specialist 2 — promise validator (design doc Stage 3).

The LLM only extracts what was said (has_promise, amount, date, method, invoice_ids) — it
never computes completeness, future-datedness, or whether the amount fits the outstanding
balance. Those are deterministic comparisons, done in `validate_promise_facts` (pure code, no
LLM). This split exists because the model got the arithmetic wrong in testing: 365,500 is
exactly 85,000 + 280,500, and it returned amount_within_outstanding=false anyway (see
docs/FAILURES.md). A language model verifying its own arithmetic has no way to catch that
kind of error — only re-doing the arithmetic in code does.
"""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import Invoice, PromiseExtraction, PromiseValidation
from collections_agent.postcall.openai_client import extract_structured
from collections_agent.postcall.specialists._common import format_transcript_for_llm
from collections_agent.postcall.transcript import Transcript

SYSTEM_PROMPT = (
    "You are extracting a promise-to-pay from a B2B collections call transcript. Extract only "
    "what the caller actually said or what a record_ptp tool call recorded — never validate, "
    "compute, or judge whether it's complete or acceptable; a separate deterministic system "
    "does that. If an amount, date, method, or invoice(s) were mentioned, extract them even if "
    "partial; leave a field null if it wasn't given. A vague answer like 'soon' or 'once you "
    "send the revised invoice' is not a date — leave promised_date null for that. "
    "confidence means your certainty that this extraction accurately reflects the transcript — "
    "not how complete or favorable the promise is. A confident 'no promise was made, or only a "
    "vague one' is high confidence, not low."
)


def extract_promise(transcript: Transcript, api_key: str) -> PromiseExtraction:
    return extract_structured(
        SYSTEM_PROMPT, format_transcript_for_llm(transcript), PromiseExtraction, api_key
    )


def _matches_invoice(invoice: Invoice, invoice_ids: list[str]) -> bool:
    return invoice.invoice_id in invoice_ids or invoice.invoice_number in invoice_ids


def validate_promise_facts(
    extraction: PromiseExtraction, invoices: list[Invoice], as_of: date
) -> PromiseValidation:
    """Pure code, no LLM — see module docstring for why this arithmetic must not be asked of
    the model."""
    is_complete = (
        extraction.has_promise
        and extraction.amount is not None
        and extraction.promised_date is not None
        and extraction.method is not None
    )
    is_future_dated = bool(extraction.promised_date and extraction.promised_date > as_of)
    method_valid = extraction.method is not None

    relevant = [inv for inv in invoices if _matches_invoice(inv, extraction.invoice_ids)]
    outstanding_total = sum(inv.outstanding() for inv in relevant) if relevant else None
    if extraction.amount is None or outstanding_total is None:
        amount_within_outstanding = False
    else:
        amount_within_outstanding = extraction.amount <= outstanding_total

    downgraded = extraction.has_promise and not (
        is_complete and is_future_dated and amount_within_outstanding and method_valid
    )

    return PromiseValidation(
        has_promise=extraction.has_promise,
        is_complete=is_complete,
        is_future_dated=is_future_dated,
        amount_within_outstanding=amount_within_outstanding,
        method_valid=method_valid,
        downgraded_to_soft_commitment=downgraded,
        amount=extraction.amount,
        promised_date=extraction.promised_date,
        method=extraction.method,
        invoice_ids=extraction.invoice_ids,
        confidence=extraction.confidence,
        notes=extraction.notes,
    )


def validate_promise(
    transcript: Transcript, invoices: list[Invoice], as_of: date, api_key: str
) -> PromiseValidation:
    extraction = extract_promise(transcript, api_key)
    return validate_promise_facts(extraction, invoices, as_of)
