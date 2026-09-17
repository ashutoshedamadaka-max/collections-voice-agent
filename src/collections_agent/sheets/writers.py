"""Typed, idempotent writers — one function per tab, each upserting by a stable key so a
re-run never duplicates rows. All conversion from Pydantic models to plain string rows
happens here; sheets/client.py only knows about grids of strings.
"""

from __future__ import annotations

from pydantic import BaseModel

from collections_agent.models.domain import PTP, Account, Dispute, Invoice, SoftCommitment
from collections_agent.sheets.client import SheetsBackend

TAB_SCHEMAS: dict[str, list[str]] = {
    "Accounts": [
        "account_id",
        "customer_name",
        "contact_name",
        "contact_role",
        "contact_phone",
        "timezone",
        "payment_terms",
        "credit_limit",
        "reliability_score",
        "preferred_language",
        "opted_out",
        "wrong_party",
        "in_active_payment_plan",
        "payment_plan_on_schedule",
    ],
    "Invoices": [
        "invoice_id",
        "account_id",
        "invoice_number",
        "amount",
        "amount_paid",
        "issue_date",
        "due_date",
        "status",
    ],
    "Call_Log": [
        "call_id",
        "account_id",
        "called_at",
        "duration_seconds",
        "outcome",
        "reason_code",
        "cost_usd",
        "recording_url",
        "qa_score",
    ],
    "PTP_Register": [
        "ptp_id",
        "account_id",
        "invoice_ids",
        "amount_promised",
        "promised_date",
        "payment_method",
        "reference_given",
        "captured_at",
        "captured_by",
        "confidence",
        "status",
        "call_id",
    ],
    "Soft_Commitments": [
        "soft_commitment_id",
        "account_id",
        "invoice_ids",
        "note",
        "captured_at",
        "captured_by",
        "call_id",
    ],
    "Disputes": [
        "dispute_id",
        "account_id",
        "invoice_id",
        "reason_code",
        "detail",
        "evidence_requested",
        "routing_target",
        "status",
        "opened_at",
        "call_id",
    ],
    "Exceptions": [
        "call_id",
        "account_id",
        "reason",
        "supervisor_notes",
        "created_at",
        "resolved",
    ],
    "Suppressed": [
        "account_id",
        "reasons",
        "checked_at",
    ],
    "Metrics": [
        "date",
        "calls_made",
        "calls_suppressed",
        "contact_rate",
        "structured_outcome_rate",
        "promise_to_pay_kept_rate",
        "disputes_surfaced",
        "human_review_rate",
    ],
    "Payments": [
        "invoice_id",
        "amount_paid",
        "paid_date",
        "method",
        "reference",
    ],
}

_KEY_FIELDS: dict[str, list[str]] = {
    "Accounts": ["account_id"],
    "Invoices": ["invoice_id"],
    "Call_Log": ["call_id"],
    "PTP_Register": ["ptp_id"],
    "Soft_Commitments": ["soft_commitment_id"],
    "Disputes": ["dispute_id"],
    "Exceptions": ["call_id"],
    "Payments": ["invoice_id", "paid_date"],
}


def _row_from_model(model: BaseModel, headers: list[str]) -> dict[str, str]:
    data = model.model_dump(mode="json")
    row: dict[str, str] = {}
    for h in headers:
        value = data.get(h, "")
        if isinstance(value, list):
            value = ";".join(str(v) for v in value)
        row[h] = "" if value is None else str(value)
    return row


def ensure_all_tabs(backend: SheetsBackend) -> None:
    for tab, headers in TAB_SCHEMAS.items():
        backend.ensure_worksheet(tab, headers)


def upsert_models(backend: SheetsBackend, tab: str, models: list[BaseModel]) -> None:
    headers = TAB_SCHEMAS[tab]
    key_fields = _KEY_FIELDS[tab]
    rows = [_row_from_model(m, headers) for m in models]
    backend.upsert_rows(tab, key_fields, rows)


def seed_accounts(backend: SheetsBackend, accounts: list[Account]) -> None:
    upsert_models(backend, "Accounts", accounts)


def seed_invoices(backend: SheetsBackend, invoices: list[Invoice]) -> None:
    upsert_models(backend, "Invoices", invoices)


def write_ptps(backend: SheetsBackend, ptps: list[PTP]) -> None:
    upsert_models(backend, "PTP_Register", ptps)


def write_soft_commitments(backend: SheetsBackend, commitments: list[SoftCommitment]) -> None:
    upsert_models(backend, "Soft_Commitments", commitments)


def write_disputes(backend: SheetsBackend, disputes: list[Dispute]) -> None:
    upsert_models(backend, "Disputes", disputes)
