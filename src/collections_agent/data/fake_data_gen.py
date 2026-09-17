"""Generates realistic fake AR data: ~25 accounts, ~60 invoices, seeded and reproducible.

This seeds Accounts and Invoices only (Step 0). Every other tab (Call_Log, PTP_Register,
Disputes, Exceptions, Suppressed, Metrics, Payments) starts empty and fills in as later
steps run against real or recorded call activity.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from collections_agent.models.domain import Account, Invoice, InvoiceStatus

DEFAULT_SEED = 42
NUM_ACCOUNTS = 25
NUM_INVOICES = 60

# Where `gen-data` writes fake accounts/invoices, and where the local (pre-Sheets) fallback
# reads them back from — see sheets/client.py:load_accounts_and_invoices.
FAKE_DATA_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "fake_ar_data.json"

INDIA_TIMEZONES = ["Asia/Kolkata"]  # single-timezone country; kept as a list for extensibility
ROLES = ["Accounts Payable Executive", "AP Manager", "Finance Manager", "Owner", "Purchase Manager"]
TERMS = ["Net 15", "Net 30", "Net 45", "Net 60"]

# Days-overdue targets chosen to land invoices across every aging bucket, including 90+
# (which the pre-call engine must suppress and route to a human, never call).
DAYS_OVERDUE_DISTRIBUTION = (
    [-3] * 8  # pre-due
    + list(range(1, 30, 3)) * 2  # 1-30
    + list(range(31, 60, 4)) * 2  # 31-60
    + list(range(61, 90, 5)) * 2  # 61-90
    + [95, 110, 130, 150]  # 90+
)


def generate_fake_ar_data(
    seed: int = DEFAULT_SEED,
    num_accounts: int = NUM_ACCOUNTS,
    num_invoices: int = NUM_INVOICES,
    as_of: date | None = None,
) -> tuple[list[Account], list[Invoice]]:
    as_of = as_of or date.today()
    fake = Faker()
    Faker.seed(seed)

    accounts: list[Account] = []
    for i in range(num_accounts):
        account_id = f"ACC-{i + 1:04d}"
        # a handful of accounts are deliberately opted-out / wrong-party / on-plan,
        # so the suppression engine has real cases to filter in Step 1's tests.
        opted_out = i == 3
        wrong_party = i == 7
        in_plan = i == 11
        accounts.append(
            Account(
                account_id=account_id,
                customer_name=fake.company(),
                contact_name=fake.name(),
                contact_role=fake.random_element(ROLES),
                contact_phone=fake.phone_number(),
                timezone=fake.random_element(INDIA_TIMEZONES),
                payment_terms=fake.random_element(TERMS),
                credit_limit=float(fake.random_int(min=50_000, max=2_000_000, step=10_000)),
                reliability_score=round(fake.random.uniform(0.4, 1.0), 2),
                preferred_language=fake.random_element(["en", "hi", "hinglish"]),
                opted_out=opted_out,
                wrong_party=wrong_party,
                in_active_payment_plan=in_plan,
                payment_plan_on_schedule=in_plan,
            )
        )

    invoices: list[Invoice] = []
    for i in range(num_invoices):
        account = accounts[i % num_accounts]
        days_overdue = DAYS_OVERDUE_DISTRIBUTION[i % len(DAYS_OVERDUE_DISTRIBUTION)]
        due_date = as_of - timedelta(days=days_overdue)
        issue_date = due_date - timedelta(days=30)
        amount = float(fake.random_int(min=5_000, max=500_000, step=500))

        # a few invoices are already fully or partially paid, to exercise suppression rules
        status = InvoiceStatus.OPEN
        amount_paid = 0.0
        if i % 17 == 0:
            status = InvoiceStatus.PAID
            amount_paid = amount
        elif i % 13 == 0:
            status = InvoiceStatus.PARTIAL
            amount_paid = round(amount * 0.4, 2)

        invoices.append(
            Invoice(
                invoice_id=f"INV-{i + 1:05d}",
                account_id=account.account_id,
                invoice_number=f"{fake.bothify('??-####').upper()}",
                amount=amount,
                amount_paid=amount_paid,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
            )
        )

    return accounts, invoices
