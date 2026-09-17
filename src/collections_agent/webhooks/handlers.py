"""Tool-call handlers, dispatched by name.

Step 2 stub behaviour: every handler validates its arguments, generates an id, and appends a
structured event to a local JSONL log — no Sheets write yet (that lands in Step 5, once
post-call orchestration in Step 4 decides what's confident enough to write back). Every
handler returns fast; nothing here makes a network call, so a tool call never causes dead air
on the live call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from collections_agent.config import get_settings
from collections_agent.sheets.client import load_accounts_and_invoices

TOOL_CALL_LOG_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "tool_calls.jsonl"


def _log_event(tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    TOOL_CALL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "logged_at": datetime.now(UTC).isoformat(),
        "tool": tool_name,
        "arguments": arguments,
        "result": result,
    }
    with TOOL_CALL_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def handle_lookup_invoices(arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = arguments["account_id"]
    try:
        _, invoices = load_accounts_and_invoices(get_settings())
    except (FileNotFoundError, NotImplementedError) as e:
        result = {"account_id": account_id, "invoices": [], "error": str(e)}
        _log_event("lookup_invoices", arguments, result)
        return result

    account_invoices = [
        {
            "invoice_number": inv.invoice_number,
            "amount": inv.amount,
            "outstanding": inv.outstanding(),
            "due_date": inv.due_date.isoformat(),
            "status": inv.status.value,
        }
        for inv in invoices
        if inv.account_id == account_id
    ]
    result = {"account_id": account_id, "invoices": account_invoices}
    _log_event("lookup_invoices", arguments, result)
    return result


def handle_record_ptp(arguments: dict[str, Any]) -> dict[str, Any]:
    """Rejects a promise date that's invalid or already in the past instead of silently
    recording it — a bare day number ("the 28th") without the current date in context is
    exactly what produced a past-dated PTP in testing; see docs/FAILURES.md. The structured
    `error` here lets the model re-ask for a corrected date rather than continuing on bad data.
    """
    raw_date = arguments.get("date", "")
    try:
        promised_date = date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        result = {
            "error": "invalid_date",
            "message": f"'{raw_date}' is not a valid ISO 8601 date (YYYY-MM-DD). Ask them to repeat it.",
        }
        _log_event("record_ptp", arguments, result)
        return result

    today = datetime.now(UTC).date()
    if promised_date < today:
        result = {
            "error": "date_in_the_past",
            "message": (
                f"{promised_date.isoformat()} is in the past (today is {today.isoformat()}). "
                "Ask the caller for a corrected date before recording this promise."
            ),
        }
        _log_event("record_ptp", arguments, result)
        return result

    result = {"ptp_id": f"PTP-{uuid.uuid4().hex[:8]}"}
    _log_event("record_ptp", arguments, result)
    return result


def handle_log_dispute(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {"dispute_id": f"DSP-{uuid.uuid4().hex[:8]}"}
    _log_event("log_dispute", arguments, result)
    return result


def handle_log_payment_claim(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {"claim_id": f"CLM-{uuid.uuid4().hex[:8]}"}
    _log_event("log_payment_claim", arguments, result)
    return result


def handle_send_document(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {"status": "queued"}
    _log_event("send_document", arguments, result)
    return result


def handle_schedule_callback(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {"callback_id": f"CB-{uuid.uuid4().hex[:8]}"}
    _log_event("schedule_callback", arguments, result)
    return result


def handle_mark_opt_out(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {"status": "confirmed"}
    _log_event("mark_opt_out", arguments, result)
    return result


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "lookup_invoices": handle_lookup_invoices,
    "record_ptp": handle_record_ptp,
    "log_dispute": handle_log_dispute,
    "log_payment_claim": handle_log_payment_claim,
    "send_document": handle_send_document,
    "schedule_callback": handle_schedule_callback,
    "mark_opt_out": handle_mark_opt_out,
}


class UnknownToolError(KeyError):
    pass


def dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(tool_name)
    if handler is None:
        raise UnknownToolError(tool_name)
    return handler(arguments)
