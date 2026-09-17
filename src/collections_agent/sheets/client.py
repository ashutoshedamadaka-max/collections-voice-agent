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
            return
        # Schema evolution: TAB_SCHEMAS can grow a new column for a tab whose header row
        # already exists in the live sheet. Without this, upsert_rows below reads the sheet's
        # (stale) header row and silently drops any field not in it — a new column is computed
        # correctly in code and then discarded forever with no error (see docs/FAILURES.md).
        missing = [h for h in headers if h not in existing_headers]
        if missing:
            start_col = len(existing_headers) + 1
            end_col = start_col + len(missing) - 1
            start_a1 = gspread.utils.rowcol_to_a1(1, start_col)
            end_a1 = gspread.utils.rowcol_to_a1(1, end_col)
            ws.update(f"{start_a1}:{end_a1}", [missing])

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

    Reads from the real Sheet once `GOOGLE_SHEET_ID` is set (Step 5) — Sheets is the single
    source of truth, so write-back writing real state there while this kept reading a static
    local snapshot forever would be an incoherent half-migration. Until `GOOGLE_SHEET_ID` is
    set, this reads the local file `gen-data` writes instead, so calls can be tested end to end
    without the service-account setup.
    """
    if settings.google_sheet_id:
        from collections_agent.sheets.readers import read_accounts, read_invoices

        backend = build_sheets_backend(settings.google_sheet_id, settings.google_service_account_json)
        return read_accounts(backend), read_invoices(backend)
    if not FAKE_DATA_PATH.exists():
        raise FileNotFoundError(f"{FAKE_DATA_PATH} not found — run `gen-data` first.")
    raw = json.loads(FAKE_DATA_PATH.read_text(encoding="utf-8"))
    accounts = [Account.model_validate(a) for a in raw["accounts"]]
    invoices = [Invoice.model_validate(i) for i in raw["invoices"]]
    return accounts, invoices
