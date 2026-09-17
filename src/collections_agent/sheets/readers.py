"""Typed readers — the inverse of writers.py's `_row_from_model`. Every tab is a plain grid of
string cells (gspread's `get_all_records()`); typed conversion back into Pydantic models
happens here, not in sheets/client.py.

Blank cells come back as `""`, which Pydantic v2 rejects for bool fields (`"True"`/`"False"`
round-trip fine; `""` raises `bool_parsing`) — a manually-cleared checkbox-style cell would
otherwise crash the reader outright. `_coerce_row` normalizes that one case before validation
rather than trying to be a general-purpose type coercer; every other field either round-trips
cleanly or should fail loudly if genuinely malformed, matching this project's existing
"reject bad data, don't fabricate" convention (see handlers.py's date validation).
"""

from __future__ import annotations

import typing
from typing import Any

from pydantic import BaseModel

from collections_agent.models.domain import (
    PTP,
    Account,
    CallLogEntry,
    Dispute,
    ExceptionEntry,
    Invoice,
    Payment,
    SoftCommitment,
)
from collections_agent.sheets.client import SheetsBackend


def _coerce_row(row: dict[str, str], model: type[BaseModel]) -> dict[str, Any]:
    """Inverse of `writers.py:_row_from_model`, which writes `""` for both a real `None` and
    a real empty list, and joins any list field with `;`. So an empty cell here is ambiguous
    only in theory — in practice every Optional field in this codebase always means "was None"
    (an empty string is never itself a meaningful stored value) — meaning any field whose type
    allows None must map "" back to None, not leave it as a literal empty string. Missing this
    for a specific field is exactly how a round-trip bug slips in silently (an Optional[str]
    field "succeeds" validation with "" instead of None, and only an equality-assertion test
    catches the mismatch — see docs/FAILURES.md's Step 6 entry). Every other (required) field
    either round-trips through `model_validate` cleanly or should fail loudly if genuinely
    malformed (this project's "reject bad data, don't fabricate" convention — see
    handlers.py's date validation).
    """
    coerced: dict[str, Any] = dict(row)
    for name, field in model.model_fields.items():
        value = coerced.get(name)
        if value != "":
            continue
        annotation = field.annotation
        if annotation is bool:
            coerced[name] = False
        elif typing.get_origin(annotation) is list:
            coerced[name] = []
        elif type(None) in typing.get_args(annotation):
            coerced[name] = None
    return coerced


def _split_list_fields(row: dict[str, str], model: type[BaseModel]) -> dict[str, Any]:
    """Splits `;`-joined list-field cells back into lists — must run before `_coerce_row`'s
    blank check would otherwise misfire on a real single-item list. An empty cell becomes []
    (handled by _coerce_row), not [""]."""
    split: dict[str, Any] = dict(row)
    for name, field in model.model_fields.items():
        if typing.get_origin(field.annotation) is list and split.get(name):
            split[name] = split[name].split(";")
    return split


def _to_model(row: dict[str, str], model: type[BaseModel]) -> BaseModel:
    return model.model_validate(_coerce_row(_split_list_fields(row, model), model))


def read_accounts(backend: SheetsBackend) -> list[Account]:
    return [_to_model(row, Account) for row in backend.read_all("Accounts")]


def read_invoices(backend: SheetsBackend) -> list[Invoice]:
    return [_to_model(row, Invoice) for row in backend.read_all("Invoices")]


def read_ptps(backend: SheetsBackend) -> list[PTP]:
    return [_to_model(row, PTP) for row in backend.read_all("PTP_Register")]


def read_soft_commitments(backend: SheetsBackend) -> list[SoftCommitment]:
    return [_to_model(row, SoftCommitment) for row in backend.read_all("Soft_Commitments")]


def read_call_log(backend: SheetsBackend) -> list[CallLogEntry]:
    return [_to_model(row, CallLogEntry) for row in backend.read_all("Call_Log")]


def read_disputes(backend: SheetsBackend) -> list[Dispute]:
    return [_to_model(row, Dispute) for row in backend.read_all("Disputes")]


def read_exceptions(backend: SheetsBackend) -> list[ExceptionEntry]:
    return [_to_model(row, ExceptionEntry) for row in backend.read_all("Exceptions")]


def read_payments(backend: SheetsBackend) -> list[Payment]:
    return [_to_model(row, Payment) for row in backend.read_all("Payments")]
