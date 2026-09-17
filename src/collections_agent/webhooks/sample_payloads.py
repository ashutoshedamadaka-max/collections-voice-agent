"""Illustrative fake Vapi payloads for the `test-webhook` dry-run command.

These are hand-written best guesses at Vapi's tool-call and end-of-call-report shapes (see
the docs-drift note in server.py), NOT observed real payloads — they exist only to exercise
our own webhook server end to end (auth, dispatch, handler bodies) before spending Vapi/OpenAI
credit on a live call. Once real payloads land in fixtures/raw/ (Step 3), correct
postcall/transcript.py against them — these dry-run samples are a separate, lower-stakes
concern and don't need to match exactly.
"""

from __future__ import annotations

from typing import Any

SAMPLE_TOOL_CALLS: list[dict[str, Any]] = [
    {
        "id": "call-lookup_invoices",
        "name": "lookup_invoices",
        "arguments": {"account_id": "ACC-0001"},
    },
    {
        "id": "call-record_ptp",
        "name": "record_ptp",
        "arguments": {
            "invoice_ids": ["INV-00001"],
            "amount": 50000,
            "date": "2026-10-15",
            "method": "NEFT",
        },
    },
    {
        "id": "call-log_dispute",
        "name": "log_dispute",
        "arguments": {
            "invoice_id": "INV-00002",
            "reason_code": "quantity_dispute",
            "detail": "customer says 10 units short on delivery",
            "evidence_requested": "signed delivery challan",
        },
    },
    {
        "id": "call-log_payment_claim",
        "name": "log_payment_claim",
        "arguments": {
            "invoice_id": "INV-00003",
            "reference": "UTR1234567890",
            "paid_on": "2026-09-01",
        },
    },
    {
        "id": "call-send_document",
        "name": "send_document",
        "arguments": {"invoice_id": "INV-00004", "doc_type": "GST invoice copy"},
    },
    {
        "id": "call-schedule_callback",
        "name": "schedule_callback",
        "arguments": {
            "name": "Rohan Mehta",
            "phone": "+91-9811122233",
            "reason": "wrong person — needs the AP manager",
            "priority": "normal",
        },
    },
    {
        "id": "call-mark_opt_out",
        "name": "mark_opt_out",
        "arguments": {"contact_id": "ACC-0001", "scope": "this_contact"},
    },
]

# Best-guess shape for a `type: end-of-call-report` server message — field names are a guess,
# not verified. Posted to /vapi/events, which today just logs the message type and acks.
SAMPLE_END_OF_CALL_REPORT: dict[str, Any] = {
    "message": {
        "type": "end-of-call-report",
        "call": {"id": "dry-run-call-1"},
        "endedReason": "customer-ended-call",
        "durationSeconds": 92,
        "cost": 0.04,
        "recordingUrl": "https://example.com/recording/dry-run-call-1.wav",
        "transcript": "Agent: Hello... Customer: ...",
    }
}
