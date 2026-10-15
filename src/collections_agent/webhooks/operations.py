"""Durable, account-scoped action receipts for a single-instance pilot.

Only trusted application code registers call scope. Tool arguments cannot choose an
account. SQLite must live on persistent storage; this is not a distributed task runner.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collections_agent.models.domain import Invoice, PromiseExtraction
from collections_agent.postcall.specialists.promise import _matches_invoice, validate_promise_facts


@contextmanager
def database(path: str | Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS call_scopes (
                call_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
                invoices TEXT NOT NULL, registered_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                account_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                result TEXT NOT NULL, status TEXT NOT NULL, owner TEXT NOT NULL,
                created_at TEXT NOT NULL, due_at TEXT NOT NULL, completed_at TEXT,
                resolution TEXT, UNIQUE(call_id, tool_call_id));
            CREATE TABLE IF NOT EXISTS suppressions (
                account_id TEXT NOT NULL, contact_id TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(account_id, contact_id));
        """)
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def register_call(path, call_id: str, account_id: str, contact_id: str, invoices: list[Invoice]):
    if not all((call_id, account_id, contact_id)) or any(i.account_id != account_id for i in invoices):
        raise ValueError("Call scope must identify one account and contact")
    payload = json.dumps([i.model_dump(mode="json") for i in invoices], sort_keys=True)
    with database(path) as db:
        existing = db.execute("SELECT * FROM call_scopes WHERE call_id=?", (call_id,)).fetchone()
        if existing:
            if (existing["account_id"], existing["contact_id"], existing["invoices"]) != (
                account_id,
                contact_id,
                payload,
            ):
                raise ValueError("A registered call scope cannot be reassigned")
            return
        db.execute(
            "INSERT INTO call_scopes VALUES (?,?,?,?,?)",
            (call_id, account_id, contact_id, payload, datetime.now(UTC).isoformat()),
        )


def suppressed(path, account_id: str, contact_id: str) -> bool:
    if not Path(path).exists():
        return False
    with database(path) as db:
        return (
            db.execute(
                "SELECT 1 FROM suppressions WHERE account_id=? AND contact_id IN (?, '*')",
                (account_id, contact_id),
            ).fetchone()
            is not None
        )


def _validate_schema(name: str, arguments: dict):
    from collections_agent.voice.tool_schemas import ALL_TOOLS

    schema = next((t["function"]["parameters"] for t in ALL_TOOLS if t["function"]["name"] == name), None)
    if schema is None:
        raise ValueError(f"unknown tool: {name}")
    if not isinstance(arguments, dict) or set(schema.get("required", [])) - arguments.keys():
        raise ValueError("Missing required tool arguments")
    for key, value in arguments.items():
        spec = schema["properties"].get(key)
        if spec is None:
            raise ValueError(f"Unexpected argument: {key}")
        kind = spec["type"]
        if kind == "string" and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{key} must be a nonempty string")
        if kind == "number" and (isinstance(value, bool) or not isinstance(value, (float, int))):
            raise ValueError(f"{key} must be a number")
        if kind == "array" and (
            not isinstance(value, list)
            or not value
            or any(not isinstance(v, str) or not v.strip() for v in value)
        ):
            raise ValueError(f"{key} must be a nonempty list of identifiers")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"Invalid {key}")


def dispatch_scoped(path, call_id: str, tool_call_id: str, name: str, arguments: dict) -> dict:
    """Validate, deduplicate and persist before acknowledging. No external side effects."""
    try:
        _validate_schema(name, arguments)
        if not call_id or not tool_call_id:
            raise ValueError("Trusted call ID and tool-call ID are required")
        payload = json.dumps(arguments, sort_keys=True, allow_nan=False)
        now = datetime.now(UTC)
        with database(path) as db:
            scope = db.execute("SELECT * FROM call_scopes WHERE call_id=?", (call_id,)).fetchone()
            if scope is None:
                raise ValueError("Unregistered call: bind account scope before accepting tools")
            prior = db.execute(
                "SELECT * FROM actions WHERE call_id=? AND tool_call_id=?", (call_id, tool_call_id)
            ).fetchone()
            if prior:
                if prior["tool"] != name or prior["arguments"] != payload:
                    raise ValueError("Tool-call ID reused with different arguments")
                return json.loads(prior["result"])
            if name != "mark_opt_out" and now - datetime.fromisoformat(scope["registered_at"]) > timedelta(
                hours=4
            ):
                raise ValueError("Call context expired; refresh through a new trusted call registration")
            invoices = [Invoice.model_validate(i) for i in json.loads(scope["invoices"])]
            account_id = scope["account_id"]
            if name == "lookup_invoices":
                if arguments["account_id"] != account_id:
                    raise ValueError("Account is outside this call's scope")
                return {
                    "account_id": account_id,
                    "invoices": [
                        {
                            "invoice_number": i.invoice_number,
                            "amount": i.amount,
                            "outstanding": i.outstanding(),
                            "due_date": str(i.due_date),
                            "status": i.status.value,
                        }
                        for i in invoices
                    ],
                }
            references = arguments.get("invoice_ids", []) + (
                [arguments["invoice_id"]] if "invoice_id" in arguments else []
            )
            for reference in references:
                if len([i for i in invoices if _matches_invoice(i, [reference])]) != 1:
                    raise ValueError("Invoice is unknown, ambiguous or outside this call's scope")
            if name == "record_ptp":
                validation = validate_promise_facts(
                    PromiseExtraction(
                        has_promise=True,
                        amount=arguments["amount"],
                        promised_date=arguments["date"],
                        method=arguments["method"],
                        invoice_ids=arguments["invoice_ids"],
                        confidence=1,
                    ),
                    invoices,
                    now.date(),
                )
                if validation.downgraded_to_soft_commitment:
                    raise ValueError(
                        "Promise requires positive in-balance amount, future date and supported invoices"
                    )
            if name == "log_payment_claim":
                from datetime import date

                if date.fromisoformat(arguments["paid_on"]) > now.date():
                    raise ValueError("A past payment claim cannot have a future date")
            action_id = hashlib.sha256(f"{call_id}:{tool_call_id}".encode()).hexdigest()[:24]
            status = "pending"
            result = {"receipt_id": action_id, "status": "queued", "completed": False}
            id_fields = {
                "record_ptp": "ptp_id",
                "log_dispute": "dispute_id",
                "log_payment_claim": "claim_id",
                "schedule_callback": "callback_id",
            }
            if name in id_fields:
                result[id_fields[name]] = action_id
            if name == "mark_opt_out":
                contact = "*" if arguments["scope"] == "whole_account" else scope["contact_id"]
                db.execute(
                    "INSERT OR IGNORE INTO suppressions VALUES (?,?,?)",
                    (account_id, contact, now.isoformat()),
                )
                result.update(status="confirmed", completed=True)
                status = "completed"
            else:
                result["message"] = (
                    "Request saved for operations follow-up; delivery or callback is not completed."
                )
            owner = (
                "billing"
                if name in {"log_dispute", "log_payment_claim", "send_document"}
                else "collections_ops"
            )
            db.execute(
                "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    call_id,
                    tool_call_id,
                    account_id,
                    name,
                    payload,
                    json.dumps(result),
                    status,
                    owner,
                    now.isoformat(),
                    (now + timedelta(hours=24)).isoformat(),
                    now.isoformat() if status == "completed" else None,
                    "suppression persisted" if status == "completed" else None,
                ),
            )
            return result
    except (ValueError, TypeError) as error:
        return {"error": "invalid_action", "message": str(error)}


def list_actions(path) -> list[dict]:
    with database(path) as db:
        return [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY created_at")]


def complete_action(path, action_id: str, resolution: str):
    if not resolution.strip():
        raise ValueError("Completion requires an operator resolution/evidence note")
    with database(path) as db:
        updated = db.execute(
            "UPDATE actions SET status='completed', completed_at=?, resolution=? "
            "WHERE action_id=? AND status='pending'",
            (datetime.now(UTC).isoformat(), resolution, action_id),
        )
        if not updated.rowcount:
            raise ValueError("No pending action with this receipt ID")


def operation_metrics(path) -> dict:
    """Current queue snapshot; acceptance is not human fulfillment."""
    now = datetime.now(UTC)
    actions = list_actions(path)
    followups = [a for a in actions if a["tool"] != "mark_opt_out"]
    due = [a for a in followups if datetime.fromisoformat(a["due_at"]) <= now]
    on_time = [a for a in due if a["completed_at"] and a["completed_at"] <= a["due_at"]]
    pending = [a for a in followups if a["status"] == "pending"]
    return {
        "as_of": now.isoformat(),
        "source": "local durable action receipts",
        "metric_version": "2",
        "followups_accepted": len(followups),
        "followups_completed": len(followups) - len(pending),
        "pending_followups": len(pending),
        "overdue_followups": sum(datetime.fromisoformat(a["due_at"]) <= now for a in pending),
        "due_followups": len(due),
        "completed_within_sla": len(on_time),
        "due_followup_sla_rate": round(len(on_time) / len(due), 4) if due else None,
        "oldest_pending_hours": max(
            ((now - datetime.fromisoformat(a["created_at"])).total_seconds() / 3600 for a in pending),
            default=None,
        ),
        "opt_outs_persisted": sum(a["tool"] == "mark_opt_out" for a in actions),
        "sla_policy": "24 calendar hours, provisional; not calibrated against customer evidence",
    }
