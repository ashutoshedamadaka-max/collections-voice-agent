"""The one account the public demo console ever talks about.

Inlined so the public demo path (`demo_live.py`, `demo_replay.py`'s clean scenario) touches
zero external services besides OpenAI and Vapi at runtime — no Google Sheets call, which
previously meant the public page both depended on, and exposed the contents of, the user's
live eval sheet to anyone who opened it (see docs/SHIP_PLAN.md).

These are the exact real values the real `GOOGLE_SHEET_ID` sheet has held for account
`ACC-0019` throughout this project's Pass 1-3 build — Udaan Supply Chain Solutions Pvt Ltd /
Gaurangi Sabharwal, the same account/invoices/narrative every fixture call, replay scenario,
and this live demo path have already used and tested against. Freezing them here as a fixture
keeps that continuity; it is not a new persona.
"""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import Account, Invoice, InvoiceStatus

DEMO_ACCOUNT_ID = "ACC-0019"

DEMO_ACCOUNT = Account(
    account_id=DEMO_ACCOUNT_ID,
    customer_name="Udaan Supply Chain Solutions Pvt Ltd",
    contact_name="Gaurangi Sabharwal",
    contact_role="AP Manager",
    contact_phone="+91-8216073375",
    timezone="Asia/Kolkata",
    payment_terms="Net 30",
    credit_limit=620000.0,
    reliability_score=0.41,
    preferred_language="en",
)

DEMO_INVOICES: list[Invoice] = [
    Invoice(
        invoice_id="INV-00019",
        account_id=DEMO_ACCOUNT_ID,
        invoice_number="USCS/26-27/0001",
        amount=282500.0,
        amount_paid=0.0,
        issue_date=date(2026, 8, 17),
        due_date=date(2026, 9, 16),
        status=InvoiceStatus.OPEN,
    ),
    Invoice(
        invoice_id="INV-00044",
        account_id=DEMO_ACCOUNT_ID,
        invoice_number="USCS/26-27/0002",
        amount=204500.0,
        amount_paid=0.0,
        issue_date=date(2026, 6, 20),
        due_date=date(2026, 7, 20),
        status=InvoiceStatus.OPEN,
    ),
]
