"""Google Sheets access, behind a small Protocol so every reader/writer is testable
against an in-memory fake without hitting the network.

Google Sheets is the single source of truth for this project (see the execution plan) — no
local database. Every tab is a plain grid of string cells; typed conversion happens in
readers.py / writers.py, not here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

import gspread
from google.oauth2.service_account import Credentials

from collections_agent.data.fake_data_gen import FAKE_DATA_PATH
from collections_agent.models.domain import Account, Invoice

if TYPE_CHECKING:
    from collections_agent.config import Settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SheetsBackend(Protocol):
    def ensure_worksheet(self, tab: str, headers: list[str]) -> None:
        """Create the worksheet with these headers if it doesn't already exist."""
        ...

    def read_all(self, tab: str) -> list[dict[str, str]]:
        """Return every data row (headers already applied as keys)."""
        ...

    def upsert_rows(self, tab: str, key_fields: list[str], rows: list[dict[str, str]]) -> None:
        """Insert or update rows, matched by the values of `key_fields`. Never duplicates."""
        ...


class GspreadSheetsBackend:
    """Real Google Sheets backend, backed by a service account."""

    def __init__(self, sheet_id: str, service_account_json_path: str):
        creds = Credentials.from_service_account_file(service_account_json_path, scopes=SCOPES)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(sheet_id)

    def ensure_worksheet(self, tab: str, headers: list[str]) -> None:
        try:
            ws = self._spreadsheet.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=tab, rows=1000, cols=max(len(headers), 10))
            ws.append_row(headers, value_input_option="RAW")
            return
        existing_headers = ws.row_values(1)
        if not existing_headers:
            ws.append_row(headers, value_input_option="RAW")

    def read_all(self, tab: str) -> list[dict[str, str]]:
        ws = self._spreadsheet.worksheet(tab)
        return ws.get_all_records()

    def upsert_rows(self, tab: str, key_fields: list[str], rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        ws = self._spreadsheet.worksheet(tab)
        headers = ws.row_values(1)
        existing = ws.get_all_records()

        def key_of(row: dict) -> tuple:
            return tuple(str(row.get(k, "")) for k in key_fields)

        existing_index = {key_of(row): i + 2 for i, row in enumerate(existing)}  # +2: header row + 1-index

        new_rows: list[list[str]] = []
        updates: list[tuple[int, list[str]]] = []
        for row in rows:
            values = [str(row.get(h, "")) for h in headers]
            existing_row_num = existing_index.get(key_of(row))
            if existing_row_num is not None:
                updates.append((existing_row_num, values))
            else:
                new_rows.append(values)

        for row_num, values in updates:
            ws.update(f"A{row_num}", [values], value_input_option="RAW")
        if new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")


def build_sheets_backend(sheet_id: str, service_account_json_path: str) -> SheetsBackend:
    return GspreadSheetsBackend(sheet_id, service_account_json_path)


def load_accounts_and_invoices(settings: Settings) -> tuple[list[Account], list[Invoice]]:
    """Account/invoice data for the pre-call engine and the `lookup_invoices` tool.

    Real Sheets reading lands in Step 5 (before write-back). Until `GOOGLE_SHEET_ID` is set,
    this always reads the local file `gen-data` writes, so calls can be tested end to end
    without the service-account setup.
    """
    if settings.google_sheet_id:
        raise NotImplementedError(
            "Reading accounts/invoices from Google Sheets isn't wired yet (lands in Step 5). "
            "Unset GOOGLE_SHEET_ID to use the local fixtures/fake_ar_data.json fallback instead."
        )
    if not FAKE_DATA_PATH.exists():
        raise FileNotFoundError(f"{FAKE_DATA_PATH} not found — run `gen-data` first.")
    raw = json.loads(FAKE_DATA_PATH.read_text(encoding="utf-8"))
    accounts = [Account.model_validate(a) for a in raw["accounts"]]
    invoices = [Invoice.model_validate(i) for i in raw["invoices"]]
    return accounts, invoices
