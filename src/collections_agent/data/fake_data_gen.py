"""Generates realistic fake AR data: ~25 accounts, ~60 invoices, seeded and reproducible.

Customers are Indian freight/logistics and packaging suppliers, matching the collections
agent's actual target segment — generic Faker company names read as obviously foreign and
undercut every demo. Invoice numbers follow a realistic Indian format (company prefix +
financial year + sequence, e.g. "KLP/25-26/0042") rather than Faker's generic "??-####".

This seeds Accounts and Invoices only (Step 0). A handful of sample Disputes are also seeded
(Step 4's reason-code taxonomy, so the Disputes tab isn't empty for a first demo) — everything
else (Call_Log, PTP_Register, Exceptions, Suppressed, Metrics, Payments) starts empty and
fills in as later steps run against real or recorded call activity.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from faker import Faker

from collections_agent.models.domain import (
    Account,
    Dispute,
    DisputeStatus,
    Invoice,
    InvoiceStatus,
    ReasonCode,
    RoutingTarget,
)

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

# Indian freight/logistics and packaging B2B customers — combined as <root> <business_type>
# <legal_form>, e.g. "Konkan Freight Carriers Pvt Ltd". Faker's generic company() provider has
# no India-specific business-name vocabulary, so this is hand-built rather than locale-swapped.
COMPANY_ROOTS = [
    "Konkan", "Deccan", "Bharat", "Shree Ganesh", "Om", "Sundaram", "Vishwas", "Anand",
    "Suraksha", "Vayu", "Prakash", "Sagar", "Himalaya", "Godavari", "Krishna", "Narmada",
    "Malabar", "Coromandel", "Nilgiri", "Aravalli", "Satpura", "Ganga", "Yamuna", "Kaveri",
    "Vindhya", "Sahyadri", "Rajdhani", "Swaraj", "Udaan", "Shakti",
]
LOGISTICS_TYPES = [
    "Freight Carriers", "Logistics", "Cargo Movers", "Roadlines", "Transport Co",
    "Warehousing", "Supply Chain Solutions", "Express Cargo",
]
PACKAGING_TYPES = [
    "Packaging Industries", "Packaging Solutions", "Corrugated Boxes", "Containers",
    "Poly Packs", "Industrial Packaging",
]
LEGAL_FORMS = ["Pvt Ltd", "Industries", "Enterprises", "& Co", "Ltd"]

DISPUTE_INVOICE_STRIDE = 7  # every 7th invoice gets a seed dispute — enough for demo variety
DISPUTE_REASON_CYCLE = [
    ReasonCode.MISSING_DOCUMENTATION,  # "missing GST invoice" in this project's domain
    ReasonCode.PO_MISMATCH,
    ReasonCode.QUANTITY_DISPUTE,
    ReasonCode.AWAITING_INTERNAL_APPROVAL,
]
DISPUTE_DETAILS = {
    ReasonCode.MISSING_DOCUMENTATION: (
        "Customer has not received a GST-compliant tax invoice for this shipment."
    ),
    ReasonCode.PO_MISMATCH: "Invoiced amount does not match the purchase order raised for this delivery.",
    ReasonCode.QUANTITY_DISPUTE: "Customer states fewer units/packages were received than invoiced.",
    ReasonCode.AWAITING_INTERNAL_APPROVAL: (
        "Invoice is with the customer's finance team pending internal sign-off."
    ),
}


def _company_name(fake: Faker) -> str:
    root = fake.random_element(COMPANY_ROOTS)
    business_type = fake.random_element(LOGISTICS_TYPES + PACKAGING_TYPES)
    legal_form = fake.random_element(LEGAL_FORMS)
    return f"{root} {business_type} {legal_form}"


def _invoice_prefix(company_name: str) -> str:
    """Company initials, e.g. "Konkan Freight Carriers Pvt Ltd" -> "KFC" — skips the legal
    form (Pvt Ltd / Industries / etc.) since real Indian invoice prefixes are drawn from the
    trading name, not the suffix."""
    skip = {"pvt", "ltd", "industries", "enterprises", "&", "co"}
    words = [w for w in company_name.split() if w.lower() not in skip]
    initials = "".join(w[0] for w in words).upper()
    return initials[:4] or "INV"


def _financial_year_label(as_of: date) -> str:
    """Indian financial year (April-March), e.g. 2026-08-24 -> "26-27"."""
    fy_start_year = as_of.year if as_of.month >= 4 else as_of.year - 1
    return f"{str(fy_start_year)[-2:]}-{str(fy_start_year + 1)[-2:]}"


def _indian_mobile_number(fake: Faker) -> str:
    first_digit = fake.random_element(["6", "7", "8", "9"])
    rest = "".join(str(fake.random_int(0, 9)) for _ in range(9))
    return f"+91-{first_digit}{rest}"


_ROUTING_TARGET_BY_REASON: dict[ReasonCode, RoutingTarget] = {
    ReasonCode.MISSING_DOCUMENTATION: RoutingTarget.BILLING,
    ReasonCode.PO_MISMATCH: RoutingTarget.SALES,
    ReasonCode.QUANTITY_DISPUTE: RoutingTarget.LOGISTICS,
    ReasonCode.AWAITING_INTERNAL_APPROVAL: RoutingTarget.OPS,
}


def generate_fake_ar_data(
    seed: int = DEFAULT_SEED,
    num_accounts: int = NUM_ACCOUNTS,
    num_invoices: int = NUM_INVOICES,
    as_of: date | None = None,
) -> tuple[list[Account], list[Invoice], list[Dispute]]:
    as_of = as_of or date.today()
    fake = Faker("en_IN")
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
                customer_name=_company_name(fake),
                contact_name=fake.name(),
                contact_role=fake.random_element(ROLES),
                contact_phone=_indian_mobile_number(fake),
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
    invoice_seq_by_account: dict[str, int] = {}
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

        seq = invoice_seq_by_account.get(account.account_id, 0) + 1
        invoice_seq_by_account[account.account_id] = seq
        prefix = _invoice_prefix(account.customer_name)
        fy_label = _financial_year_label(issue_date)
        invoice_number = f"{prefix}/{fy_label}/{seq:04d}"

        invoices.append(
            Invoice(
                invoice_id=f"INV-{i + 1:05d}",
                account_id=account.account_id,
                invoice_number=invoice_number,
                amount=amount,
                amount_paid=amount_paid,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
            )
        )

    # A handful of sample disputes, using the design doc's reason-code taxonomy, so the
    # Disputes tab has realistic rows to look at before any real call has happened. call_id is
    # a placeholder ("SEED-<n>") — these were never actually raised on a call.
    disputes: list[Dispute] = []
    disputed_invoices = [
        inv
        for idx, inv in enumerate(invoices)
        if inv.status == InvoiceStatus.OPEN and idx % DISPUTE_INVOICE_STRIDE == 0
    ]
    for n, inv in enumerate(disputed_invoices):
        reason = DISPUTE_REASON_CYCLE[n % len(DISPUTE_REASON_CYCLE)]
        disputes.append(
            Dispute(
                dispute_id=f"DSP-SEED-{n + 1:04d}",
                account_id=inv.account_id,
                invoice_id=inv.invoice_id,
                reason_code=reason,
                detail=DISPUTE_DETAILS[reason],
                evidence_requested="GST invoice copy" if reason == ReasonCode.MISSING_DOCUMENTATION else None,
                routing_target=_ROUTING_TARGET_BY_REASON[reason],
                status=DisputeStatus.OPEN,
                opened_at=datetime.combine(inv.due_date, datetime.min.time(), tzinfo=UTC),
                call_id=f"SEED-{n + 1}",
            )
        )

    return accounts, invoices, disputes
