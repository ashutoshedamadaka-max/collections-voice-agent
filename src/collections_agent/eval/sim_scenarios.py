"""Loads `config/sim_scenarios.yaml` and turns each scenario into concrete call data.

A scenario in the YAML is written relative to "today" (an invoice due 40 days ago, a promise 9
days out) so it never goes stale the way fixtures pinned to a generated dataset did
(docs/FAILURES.md, 2026-09-17, "fixtures coupled to generated data went stale silently").
`materialize()` resolves those relative values against a concrete `as_of` date, builds the
Account / Invoice / ContextPack objects the real prompt renderer needs, and fills the
customer's brief with the exact dates the expectations will be checked against.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from collections_agent.models.domain import Account, ContextPack, Invoice
from collections_agent.precall.context_pack import build_context_pack

SCENARIOS_PATH = Path(__file__).resolve().parents[3] / "config" / "sim_scenarios.yaml"

ACCOUNT_ID = "ACC-SIM-0001"


class InvoiceSpec(BaseModel):
    number: str
    amount: float
    due_days_ago: int
    amount_paid: float = 0.0


class ToolExpectation(BaseModel):
    """One tool call the agent must make. Every field other than `name` is optional and only
    checked when set — see the matcher reference at the top of the YAML file."""

    name: str
    invoice: str | None = None  # "primary" | "other"
    amount: str | float | None = None  # "full" | "other_full" | a number
    date: str | None = None  # "promise_date" | "paid_on_date" | an ISO date
    method: str | None = None
    reason_code: str | list[str] | None = None
    reference: str | None = None
    priority: str | list[str] | None = None
    scope: str | list[str] | None = None


class PostcallExpectation(BaseModel):
    outcome: list[str] | None = None
    promise_captured: bool | None = None
    promise_complete: bool | None = None
    dispute_existed: bool | None = None
    compliance_violated: bool | None = None
    write_decision: str | None = None


class Expectation(BaseModel):
    tools: list[ToolExpectation] = Field(default_factory=list)
    forbid_tools: list[str] = Field(default_factory=list)
    postcall: PostcallExpectation = Field(default_factory=PostcallExpectation)


class Scenario(BaseModel):
    id: str
    title: str
    brief: str
    vars: dict[str, int] = Field(default_factory=dict)
    invoices: list[InvoiceSpec] | None = None
    wrong_person: bool = False
    language: str = "en"
    expect: Expectation = Field(default_factory=Expectation)


class ScenarioDefaults(BaseModel):
    customer_name: str
    contact_name: str
    contact_role: str
    payment_terms: str
    invoices: list[InvoiceSpec]


class MaterializedScenario(BaseModel):
    """A scenario resolved against one concrete date — everything the simulator and the
    checks need, with no placeholders left."""

    scenario: Scenario
    as_of: date
    account: Account
    invoices: list[Invoice]
    context_pack: ContextPack
    customer_brief: str
    resolved_dates: dict[str, date]

    @property
    def primary_invoice(self) -> Invoice:
        return next(i for i in self.invoices if i.invoice_id == self.context_pack.primary_invoice_id)

    @property
    def other_invoices(self) -> list[Invoice]:
        return [i for i in self.invoices if i.invoice_id != self.context_pack.primary_invoice_id]


def load_scenarios(path: Path = SCENARIOS_PATH) -> tuple[ScenarioDefaults, list[Scenario]]:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = ScenarioDefaults.model_validate(raw["defaults"])
    scenarios = [Scenario.model_validate(s) for s in raw["scenarios"]]
    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario ids in {path.name}: {sorted(duplicates)}")
    return defaults, scenarios


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def spoken_date(d: date) -> str:
    """How a person would say a date on the phone, e.g. 'Friday the 23rd of October 2026'. The
    year is included on purpose: without it, the simulated customer filled in its own year
    (2023) and insisted on it, which made a harness bug look like an agent bug (first sim-eval
    run, 2026-10-14)."""
    return f"{d.strftime('%A')} the {_ordinal(d.day)} of {d.strftime('%B')} {d.year}"


def next_occurrence_of_day(day: int, as_of: date) -> date:
    """The next date on or after `as_of` whose day-of-month is `day` — the same rule the agent
    prompt gives for a bare day number ("the 28th")."""
    candidate_year, candidate_month = as_of.year, as_of.month
    for _ in range(13):
        try:
            candidate = date(candidate_year, candidate_month, day)
        except ValueError:
            candidate = None
        if candidate is not None and candidate >= as_of:
            return candidate
        candidate_month += 1
        if candidate_month > 12:
            candidate_month, candidate_year = 1, candidate_year + 1
    raise ValueError(f"no upcoming date with day {day}")


def materialize(scenario: Scenario, defaults: ScenarioDefaults, as_of: date) -> MaterializedScenario:
    specs = scenario.invoices or defaults.invoices
    invoices = [
        Invoice(
            invoice_id=f"INV-SIM-{i + 1:03d}",
            account_id=ACCOUNT_ID,
            invoice_number=spec.number,
            amount=spec.amount,
            amount_paid=spec.amount_paid,
            issue_date=as_of - timedelta(days=spec.due_days_ago + 30),
            due_date=as_of - timedelta(days=spec.due_days_ago),
        )
        for i, spec in enumerate(specs)
    ]
    account = Account(
        account_id=ACCOUNT_ID,
        customer_name=defaults.customer_name,
        contact_name=defaults.contact_name,
        contact_role=defaults.contact_role,
        contact_phone="+91 90000 00000",
        timezone="Asia/Kolkata",
        payment_terms=defaults.payment_terms,
        credit_limit=1_000_000,
        preferred_language=scenario.language,
    )
    context_pack = build_context_pack(account, invoices, [], [], [], as_of=as_of)

    resolved: dict[str, date] = {}
    fills: dict[str, str] = {}
    if "promise_in_days" in scenario.vars:
        promise = as_of + timedelta(days=scenario.vars["promise_in_days"])
        # A bare day number resolves to the next occurrence of that day, which for any offset
        # under ~28 days is the promise date itself — but compute it the same way the agent is
        # told to, rather than assuming.
        promise = next_occurrence_of_day(promise.day, as_of)
        resolved["promise_date"] = promise
        fills["promise_date"] = spoken_date(promise)
        fills["promise_day_number"] = f"the {_ordinal(promise.day)}"
    if "paid_days_ago" in scenario.vars:
        paid = as_of - timedelta(days=scenario.vars["paid_days_ago"])
        resolved["paid_on_date"] = paid
        fills["paid_on_date"] = spoken_date(paid)

    try:
        brief = scenario.brief.format(**fills)
    except KeyError as e:
        raise ValueError(f"scenario {scenario.id!r} uses placeholder {e} but sets no matching var") from e

    return MaterializedScenario(
        scenario=scenario,
        as_of=as_of,
        account=account,
        invoices=invoices,
        context_pack=context_pack,
        customer_brief=brief.strip(),
        resolved_dates=resolved,
    )


def today_utc() -> date:
    """The simulator's 'today'. The live record_ptp handler rejects past dates against UTC
    today, so the simulation uses the same clock to keep the two consistent."""
    return datetime.now(UTC).date()
