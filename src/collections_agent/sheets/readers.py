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

from typing import Any

from pydantic import BaseModel

from collections_agent.models.domain import Account, Invoice
from collections_agent.sheets.client import SheetsBackend


def _coerce_row(row: dict[str, str], model: type[BaseModel]) -> dict[str, Any]:
    coerced: dict[str, Any] = dict(row)
    for name, field in model.model_fields.items():
        if coerced.get(name) == "" and field.annotation is bool:
            coerced[name] = False
    return coerced


def read_accounts(backend: SheetsBackend) -> list[Account]:
    return [Account.model_validate(_coerce_row(row, Account)) for row in backend.read_all("Accounts")]


def read_invoices(backend: SheetsBackend) -> list[Invoice]:
    return [Invoice.model_validate(_coerce_row(row, Invoice)) for row in backend.read_all("Invoices")]
