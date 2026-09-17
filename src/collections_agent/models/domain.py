"""Core domain models shared across the pre-call, voice, post-call, and follow-through layers.

These are plain Pydantic models with no I/O — every function that takes or returns them
(suppression, priority scoring, context-pack assembly, the post-call specialists) is a pure
function and unit-testable without touching Google Sheets, Vapi, or OpenAI.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AgingBucket(StrEnum):
    PRE_DUE = "pre_due"
    D1_30 = "1-30"
    D31_60 = "31-60"
    D61_90 = "61-90"
    D90_PLUS = "90+"


class ReasonCode(StrEnum):
    INVOICE_NOT_RECEIVED = "invoice_not_received"
    AWAITING_INTERNAL_APPROVAL = "awaiting_internal_approval"
    PO_MISMATCH = "po_mismatch"
    MISSING_DOCUMENTATION = "missing_documentation"
    QUALITY_DISPUTE = "quality_dispute"
    QUANTITY_DISPUTE = "quantity_dispute"
    PRICING_DISPUTE = "pricing_dispute"
    PAYMENT_RUN_TIMING = "payment_run_timing"
    CASH_FLOW = "cash_flow"
    WRONG_CONTACT = "wrong_contact"
    PAID_ALREADY = "paid_already"


class PaymentMethod(StrEnum):
    NEFT = "NEFT"
    RTGS = "RTGS"
    UPI = "UPI"
    CHEQUE = "cheque"
    PORTAL = "portal"


class PTPStatus(StrEnum):
    OPEN = "open"
    KEPT = "kept"
    BROKEN = "broken"
    PARTIAL = "partial"
    SUPERSEDED = "superseded"


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    PAID = "paid"


class DisputeStatus(StrEnum):
    OPEN = "open"
    ROUTED = "routed"
    RESOLVED = "resolved"


class RoutingTarget(StrEnum):
    BILLING = "billing"
    SALES = "sales"
    OPS = "ops"
    LOGISTICS = "logistics"


class CallOutcomeType(StrEnum):
    PROMISE_TO_PAY = "promise_to_pay"
    SOFT_COMMITMENT = "soft_commitment"
    ALREADY_PAID = "already_paid"
    DISPUTE = "dispute"
    DOCUMENT_REQUESTED = "document_requested"
    WRONG_PERSON = "wrong_person"
    ESCALATION_REQUESTED = "escalation_requested"
    OPT_OUT = "opt_out"
    NO_ANSWER = "no_answer"
    HOSTILE = "hostile"


class Account(BaseModel):
    account_id: str
    customer_name: str
    contact_name: str
    contact_role: str
    contact_phone: str
    timezone: str = Field(description="IANA timezone, e.g. 'Asia/Kolkata'")
    payment_terms: str
    credit_limit: float
    reliability_score: float = Field(default=1.0, ge=0.0, le=1.0)
    preferred_language: str = "en"
    opted_out: bool = False
    wrong_party: bool = False
    in_active_payment_plan: bool = False
    payment_plan_on_schedule: bool = False


class Invoice(BaseModel):
    invoice_id: str
    account_id: str
    invoice_number: str
    amount: float
    amount_paid: float = 0.0
    issue_date: date
    due_date: date
    status: InvoiceStatus = InvoiceStatus.OPEN

    def outstanding(self) -> float:
        return round(self.amount - self.amount_paid, 2)

    def aging_bucket(self, as_of: date) -> AgingBucket:
        days_overdue = (as_of - self.due_date).days
        if days_overdue < 0:
            return AgingBucket.PRE_DUE
        if days_overdue <= 30:
            return AgingBucket.D1_30
        if days_overdue <= 60:
            return AgingBucket.D31_60
        if days_overdue <= 90:
            return AgingBucket.D61_90
        return AgingBucket.D90_PLUS


class PTP(BaseModel):
    ptp_id: str
    account_id: str
    invoice_ids: list[str]
    amount_promised: float
    promised_date: date
    payment_method: PaymentMethod
    reference_given: str | None = None
    captured_at: datetime
    captured_by: str = Field(description="agent version, e.g. 'v1'")
    confidence: float = Field(ge=0.0, le=1.0)
    status: PTPStatus = PTPStatus.OPEN
    call_id: str


class SoftCommitment(BaseModel):
    soft_commitment_id: str
    account_id: str
    invoice_ids: list[str]
    note: str
    captured_at: datetime
    captured_by: str
    call_id: str


class Dispute(BaseModel):
    dispute_id: str
    account_id: str
    invoice_id: str
    reason_code: ReasonCode
    detail: str
    evidence_requested: str | None = None
    routing_target: RoutingTarget
    status: DisputeStatus = DisputeStatus.OPEN
    opened_at: datetime
    call_id: str


class CallLogEntry(BaseModel):
    call_id: str
    account_id: str
    called_at: datetime
    duration_seconds: int
    outcome: CallOutcomeType
    reason_code: ReasonCode | None = None
    cost_usd: float | None = None
    recording_url: str | None = None
    qa_score: float | None = None


class SuppressionResult(BaseModel):
    suppressed: bool
    reasons: list[str] = Field(default_factory=list)


class PriorityWeights(BaseModel):
    bucket_weight: dict[AgingBucket, float]
    balance_weight_cap: float = 3.0
    balance_weight_denominator: float = 10_000.0
    ptp_reliability_min: float = 0.5
    ptp_reliability_broken_penalty: float = 0.5
    contactability_wrong_person: float = 0.7
    contactability_default: float = 1.0


class WriteDecision(StrEnum):
    AUTO_WRITE = "auto_write"
    EXCEPTION_QUEUE = "exception_queue"


class OutcomeExtraction(BaseModel):
    """Specialist 1 — structured call outcome, reason code, and what should happen next."""

    outcome: CallOutcomeType
    reason_code: ReasonCode | None = None
    next_action: str
    confidence: float = Field(ge=0.0, le=1.0)


class PromiseExtraction(BaseModel):
    """What the LLM extracts from the transcript for specialist 2 — facts only. Completeness,
    date, and amount checks are arithmetic/comparisons, computed deterministically in code
    (see promise.py's validate_promise_facts) rather than asked of the model — an LLM doing
    that arithmetic produced a wrong answer in testing (docs/FAILURES.md)."""

    has_promise: bool
    amount: float | None = None
    promised_date: date | None = None
    method: PaymentMethod | None = None
    invoice_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Certainty that this extraction accurately reflects the transcript — "
        "not how complete or favorable the promise is. A confident 'no promise was made' is "
        "high confidence, not low.",
    )
    notes: str = ""


class PromiseValidation(BaseModel):
    """Specialist 2's final output — PromiseExtraction plus the deterministic checks computed
    in code: is the PTP complete, future-dated, within the outstanding balance, and a valid
    method? Incomplete promises downgrade to a soft commitment rather than a PTP."""

    has_promise: bool
    is_complete: bool = Field(description="amount, date, and method were all given")
    is_future_dated: bool = True
    amount_within_outstanding: bool = True
    method_valid: bool = True
    downgraded_to_soft_commitment: bool = False
    amount: float | None = None
    promised_date: date | None = None
    method: PaymentMethod | None = None
    invoice_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class DisputeClassification(BaseModel):
    """Specialist 3 — reason code, invoice, evidence requested, and routing target."""

    has_dispute: bool
    invoice_id: str | None = None
    reason_code: ReasonCode | None = None
    detail: str = ""
    evidence_requested: str | None = None
    routing_target: RoutingTarget | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ComplianceExtraction(BaseModel):
    """What the LLM judges for specialist 4 — qualitative compliance facts only. Whether
    stated figures are arithmetically consistent with the actual invoice data is a deterministic
    check, computed in code (see compliance.py's check_arithmetic_consistency), not asked of
    the model — a model grading its own arithmetic has the same failure mode as the promise
    validator's amount check (docs/FAILURES.md)."""

    disclosed_automated: bool
    verified_authority: bool
    stayed_within_permitted_facts: bool
    promised_discount_or_waiver: bool
    threatened_consequences: bool
    qa_score: float = Field(ge=0.0, le=1.0)
    notes: str = ""
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Certainty in this judgment being correct — not how compliant the call "
        "was. A confident 'no violations found' is high confidence, not low.",
    )


class ComplianceReview(BaseModel):
    """Specialist 4's final output — ComplianceExtraction plus misstated_total, a deterministic
    check computed in code. A misstated total is treated as a compliance violation, not a QA
    nicety: it caps qa_score regardless of what the model's qualitative judgment scored."""

    disclosed_automated: bool
    verified_authority: bool
    stayed_within_permitted_facts: bool
    promised_discount_or_waiver: bool
    threatened_consequences: bool
    misstated_total: bool = False
    qa_score: float = Field(ge=0.0, le=1.0)
    notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class PostCallAnalysis(BaseModel):
    """The supervisor's merged output — what Step 5's write-back actually consumes."""

    call_id: str
    account_id: str
    outcome: OutcomeExtraction
    promise: PromiseValidation
    dispute: DisputeClassification
    compliance: ComplianceReview
    overall_confidence: float = Field(ge=0.0, le=1.0)
    write_decision: WriteDecision
    supervisor_notes: str = ""
    exception_reason: str = Field(
        default="",
        description="Comma-joined short tags (low_confidence, disagreement, "
        "compliance_violation) for the Exceptions tab's `reason` column — computed from the "
        "same buckets that produced supervisor_notes, not re-parsed from that free text.",
    )


class ExceptionEntry(BaseModel):
    """Backs the `Exceptions` tab — the only tab that didn't already have a typed domain model.
    `resolved` starts blank; a human fills it in directly in the Sheet (see docs/FAILURES.md-
    style reasoning: no review UI is being built in Step 5, the Sheet itself is the surface)."""

    call_id: str
    account_id: str
    reason: str
    supervisor_notes: str
    created_at: datetime
    resolved: str = ""


class ContextPack(BaseModel):
    account_id: str
    contact_name: str
    contact_role: str
    customer_name: str
    invoices: list[Invoice]
    total_outstanding: float
    payment_terms: str
    preferred_language: str
    prior_promises: list[PTP] = Field(default_factory=list)
    open_disputes: list[Dispute] = Field(default_factory=list)
    last_contact_date: datetime | None = None
    last_contact_outcome: CallOutcomeType | None = None
    bucket: AgingBucket
