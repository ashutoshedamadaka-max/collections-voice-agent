from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from collections_agent.models.domain import Account, Invoice, InvoiceStatus
from collections_agent.sheets.client import SheetsBackend


class InMemorySheetsBackend:
    """Fake SheetsBackend for tests — same interface as GspreadSheetsBackend, no network."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[dict[str, str]]] = {}
        self.headers: dict[str, list[str]] = {}

    def ensure_worksheet(self, tab: str, headers: list[str]) -> None:
        self.tabs.setdefault(tab, [])
        self.headers.setdefault(tab, headers)

    def read_all(self, tab: str) -> list[dict[str, str]]:
        return list(self.tabs.get(tab, []))

    def upsert_rows(self, tab: str, key_fields: list[str], rows: list[dict[str, str]]) -> None:
        existing = self.tabs.setdefault(tab, [])

        def key_of(row: dict) -> tuple:
            return tuple(str(row.get(k, "")) for k in key_fields)

        index = {key_of(row): i for i, row in enumerate(existing)}
        for row in rows:
            k = key_of(row)
            if k in index:
                existing[index[k]] = row
            else:
                existing.append(row)
                index[k] = len(existing) - 1


@pytest.fixture
def fake_sheets_backend() -> SheetsBackend:
    return InMemorySheetsBackend()


NOW = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)  # a Tuesday, mid-day IST


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def as_of() -> date:
    return NOW.date()


@pytest.fixture
def base_account() -> Account:
    return Account(
        account_id="ACC-0001",
        customer_name="Test Traders Pvt Ltd",
        contact_name="Priya Sharma",
        contact_role="AP Manager",
        contact_phone="+91-9800000000",
        timezone="Asia/Kolkata",
        payment_terms="Net 30",
        credit_limit=500_000,
        reliability_score=0.9,
        preferred_language="en",
    )


@pytest.fixture
def make_invoice(as_of: date) -> Callable[..., Invoice]:
    """Factory: make_invoice(account_id, days_overdue=10, amount=100_000, ...) -> Invoice."""

    counter = {"n": 0}

    def _make(
        account_id: str,
        days_overdue: int = 10,
        amount: float = 100_000,
        amount_paid: float = 0.0,
        status: InvoiceStatus = InvoiceStatus.OPEN,
        invoice_id: str | None = None,
    ) -> Invoice:
        counter["n"] += 1
        due_date = as_of - timedelta(days=days_overdue)
        return Invoice(
            invoice_id=invoice_id or f"INV-{counter['n']:05d}",
            account_id=account_id,
            invoice_number=f"TT-{counter['n']:04d}",
            amount=amount,
            amount_paid=amount_paid,
            issue_date=due_date - timedelta(days=30),
            due_date=due_date,
            status=status,
        )

    return _make


@pytest.fixture
def sample_context_pack(base_account, make_invoice, as_of):
    from collections_agent.precall.context_pack import build_context_pack

    inv = make_invoice(base_account.account_id, days_overdue=10, amount=123_456)
    return build_context_pack(base_account, [inv], [], [], [], as_of=as_of)
