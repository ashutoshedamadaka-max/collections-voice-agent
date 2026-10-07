"""JSON-Schema definitions for the 6 live tools the voice agent can call mid-conversation.

Every tool returns fast and never blocks the conversation — writes are queued and reconciled
post-call (see postcall/pipeline.py and sheets/writers.py), so a slow Sheets write never
causes dead air. These dicts are consumed by voice/assistant_config.py to build the Vapi
assistant payload, and by webhooks/handlers.py's dispatch table (by `name`) to validate
incoming tool-call arguments.
"""

from __future__ import annotations

from typing import Any

# Appended to every action tool's description — the model narrated "I will log this as a
# dispute" twice in testing and never actually called log_dispute (see docs/FAILURES.md).
# Vapi doesn't expose a tool-choice/forced-function setting (checked against its current
# OpenAPI spec — no such field exists), so the fix is an explicit, repeated instruction at
# both the tool-description level (here) and the prompt level (system_prompt.j2).
DO_NOT_NARRATE = (
    " Do not say you will do this or describe doing it — call this function, then report the outcome."
)

# Spoken if the webhook hasn't responded within a few seconds (filler) or times out entirely
# (fallback) — verified against Vapi's current OpenAPI schema (CreateFunctionToolDTO.messages /
# ToolMessageDelayed / ToolMessageFailed), since this is exactly the class of bug that produced
# dead air in testing: an unreachable server otherwise fails silently, not loudly (see
# docs/FAILURES.md). `role: "system"` on the failure message feeds it to the model as a hint
# rather than a fixed script, so it can acknowledge whatever was actually being recorded.
TOOL_TIMEOUT_MESSAGES: list[dict[str, Any]] = [
    {
        "type": "request-response-delayed",
        "content": "One moment, still working on that.",
        "timingMilliseconds": 4000,
    },
    {
        "type": "request-failed",
        "role": "system",
        "content": (
            "The system didn't confirm this in time. Acknowledge what the caller just told "
            "you, tell them it will be confirmed in writing, and continue the call naturally "
            "— do not go silent and do not repeat the request."
        ),
    },
]

LOOKUP_INVOICES = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "lookup_invoices",
        "description": "Read-only, idempotent. Look up the invoice list for an account.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
            },
            "required": ["account_id"],
        },
    },
}

RECORD_PTP = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "record_ptp",
        "description": (
            "Call this only after the caller explicitly commits to pay a specific amount on a "
            "specific date by a stated method, and you have confirmed all three back. An "
            "approval estimate or possible payment date is not a promise." + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_ids": {"type": "array", "items": {"type": "string"}},
                "amount": {"type": "number"},
                "date": {"type": "string", "description": "ISO 8601 date, e.g. 2026-09-30"},
                "method": {"type": "string", "enum": ["NEFT", "RTGS", "UPI", "cheque", "portal"]},
            },
            "required": ["invoice_ids", "amount", "date", "method"],
        },
    },
}

LOG_DISPUTE = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "log_dispute",
        "description": (
            "Call this immediately when the customer disputes any invoice or charge. Never "
            "argue the claim — just capture and route it. Waiting for the customer's own finance "
            "approval is a payment delay, not a dispute; do not call this tool for that alone."
            + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "reason_code": {
                    "type": "string",
                    "enum": [
                        "invoice_not_received",
                        "po_mismatch",
                        "missing_documentation",
                        "quality_dispute",
                        "quantity_dispute",
                        "pricing_dispute",
                        "payment_run_timing",
                        "cash_flow",
                        "wrong_contact",
                        "paid_already",
                    ],
                },
                "detail": {"type": "string"},
                "evidence_requested": {"type": "string"},
            },
            "required": ["invoice_id", "reason_code", "detail"],
        },
    },
}

LOG_PAYMENT_CLAIM = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "log_payment_claim",
        "description": (
            "Call this immediately when the customer claims an invoice is already paid and "
            "has given a reference and date." + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "reference": {"type": "string", "description": "UTR or cheque number"},
                "paid_on": {"type": "string", "description": "ISO 8601 date"},
            },
            "required": ["invoice_id", "reference", "paid_on"],
        },
    },
}

SEND_DOCUMENT = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "send_document",
        "description": (
            "Call this immediately when the customer asks for a document, once you know which "
            "one. Queues it to be drafted — never sent automatically." + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "doc_type": {"type": "string"},
            },
            "required": ["invoice_id", "doc_type"],
        },
    },
}

SCHEDULE_CALLBACK = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "schedule_callback",
        "description": (
            "Call this to hand off to a human: wrong person, escalation request, unanswerable "
            "question, or a separate overdue invoice deferred from this call. Only a reason is "
            "required; do not ask for a callback date just to use this tool." + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "reason": {"type": "string"},
                "priority": {"type": "string", "enum": ["normal", "high"]},
            },
            "required": ["reason"],
        },
    },
}

MARK_OPT_OUT = {
    "type": "function",
    "messages": TOOL_TIMEOUT_MESSAGES,
    "function": {
        "name": "mark_opt_out",
        "description": (
            "Call this immediately when asked to stop calling. Acknowledge out loud, call this, "
            "then end the call." + DO_NOT_NARRATE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["this_contact", "whole_account"]},
            },
            "required": ["contact_id", "scope"],
        },
    },
}

ALL_TOOLS: list[dict[str, Any]] = [
    LOOKUP_INVOICES,
    RECORD_PTP,
    LOG_DISPUTE,
    LOG_PAYMENT_CLAIM,
    SEND_DOCUMENT,
    SCHEDULE_CALLBACK,
    MARK_OPT_OUT,
]

TOOL_NAMES: list[str] = [t["function"]["name"] for t in ALL_TOOLS]
