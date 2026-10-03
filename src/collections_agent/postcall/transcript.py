"""Transcript retrieval and parsing.

`parse_transcript` is anchored to real payloads pulled from live test calls (see
fixtures/raw/*.json) — a tool invocation is always two separate messages: a `tool_calls`
message (`role: "tool_calls"`, `toolCalls: [{"id", "function": {"name", "arguments"}}]`,
arguments JSON-encoded as a string) and a later `tool_call_result` message (`role:
"tool_call_result"`, `name`, `result`, `toolCallId` matching the call's `id`). An earlier
version of this parser predated any real payload and matched both messages per invocation,
double-counting every tool call. `save_raw` still always writes the untouched payload to
fixtures/raw/ *before* parsing is attempted, so a payload shape this can't handle never loses
data — but treat this as ground-truth-checked now, not a guess.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from collections_agent.models.domain import Invoice, InvoiceStatus

RAW_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "raw"
PARSED_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "transcripts"

# Matches the per-call system prompt's own "You are speaking with X (role) at Y." line —
# stable across the prompt template's revisions seen so far (see account_facts_from_system_prompt).
_SPEAKING_WITH_RE = re.compile(r"You are speaking with ([^(]+) \(([^)]+)\) at ([^.]+)\.")
# Matches the older, pre-redesign "Invoices: A (due D1, outstanding O1); B (due D2, ...)" line
# format (see docs/FAILURES.md, "one-invoice-per-call redesign").
_INVOICE_LINE_RE = re.compile(r"([A-Z]{2,}-?\d[\w/-]*)\s*\(due\s+([\d-]+),\s*outstanding\s+([\d,]+\.\d+)\)")


class TranscriptTurn(BaseModel):
    role: str
    content: str


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None


class Transcript(BaseModel):
    call_id: str
    turns: list[TranscriptTurn]
    tool_calls: list[ToolCallRecord]
    started_at: datetime | None = None
    duration_seconds: float | None = None
    cost_usd: float | None = None
    ended_reason: str | None = None
    recording_url: str | None = None


def save_raw(call_id: str, raw: dict[str, Any], raw_dir: Path = RAW_DIR) -> Path:
    """Write the untouched Vapi payload to disk. Always call this before parse_transcript."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{call_id}.json"
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


def _extract_tool_calls(messages: list[dict[str, Any]]) -> list[ToolCallRecord]:
    """One record per actual invocation, not per message: a `tool_calls` message carries the
    name/arguments, a separate `tool_call_result` message (matched by `toolCallId`) carries
    the result — counting both per call is exactly the double-count bug this replaced.
    """
    results_by_call_id: dict[str, Any] = {
        m["toolCallId"]: m.get("result")
        for m in messages
        if m.get("role") == "tool_call_result" and m.get("toolCallId")
    }

    records: list[ToolCallRecord] = []
    for m in messages:
        for call in m.get("toolCalls") or []:
            fn = call.get("function", {})
            raw_arguments = fn.get("arguments")
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments) if raw_arguments else {}
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = raw_arguments or {}
            records.append(
                ToolCallRecord(
                    name=fn.get("name", "unknown"),
                    arguments=arguments,
                    result=results_by_call_id.get(call.get("id")),
                )
            )
    return records


def parse_transcript(raw: dict[str, Any]) -> Transcript:
    """See module docstring — anchored to real pulled payloads, not a guess."""
    call_id = raw.get("id", "unknown")
    messages = raw.get("messages") or raw.get("artifact", {}).get("messages", [])

    turns = [
        TranscriptTurn(role=m["role"], content=m.get("message") or m.get("content") or "")
        for m in messages
        if m.get("role") in ("user", "assistant", "bot")
    ]
    tool_calls = _extract_tool_calls(messages)

    cost = raw.get("cost")
    cost_usd = cost if isinstance(cost, (int, float)) else None

    # There is no top-level "durationSeconds" field on a real Vapi call payload — verified
    # against every payload pulled so far — so this always silently returned None before.
    # startedAt/endedAt are both always present; derive duration from them instead.
    started_at = _parse_iso(raw.get("startedAt"))
    ended_at = _parse_iso(raw.get("endedAt"))
    duration_seconds = (ended_at - started_at).total_seconds() if started_at and ended_at else None

    return Transcript(
        call_id=call_id,
        turns=turns,
        tool_calls=tool_calls,
        started_at=started_at,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        ended_reason=raw.get("endedReason"),
        recording_url=raw.get("recordingUrl") or raw.get("artifact", {}).get("recordingUrl"),
    )


def account_facts_from_system_prompt(raw: dict[str, Any]) -> dict[str, Any]:
    """Recovers account/invoice facts for a call whose account no longer exists in the current
    dataset (see docs/FAILURES.md, "fixtures coupled to generated data went stale silently") —
    the exact facts the agent was given for that real call are embedded verbatim in its own
    system prompt message. Parsing them out of there is still reading real, recorded data; it's
    just not coming from a live Sheet row. Returns customer_name/contact_name/contact_role
    (best-effort — "Unknown"/empty if the prompt doesn't match the expected pattern) and a list
    of `Invoice` objects (account_id="historical") built from whatever invoice lines matched.
    """
    messages = raw.get("messages") or raw.get("artifact", {}).get("messages", [])
    sysmsg = next((m for m in messages if m.get("role") == "system"), None)
    text = sysmsg.get("message", "") if sysmsg else ""

    contact_name = contact_role = customer_name = None
    match = _SPEAKING_WITH_RE.search(text)
    if match:
        contact_name, contact_role, customer_name = (g.strip() for g in match.groups())

    invoices = [
        Invoice(
            invoice_id=inv_number,
            account_id="historical",
            invoice_number=inv_number,
            amount=float(amount_str.replace(",", "")),
            issue_date=date.fromisoformat(due_str),
            due_date=date.fromisoformat(due_str),
            status=InvoiceStatus.OPEN,
        )
        for inv_number, due_str, amount_str in _INVOICE_LINE_RE.findall(text)
    ]

    return {
        "customer_name": customer_name or "Unknown (historical account)",
        "contact_name": contact_name or "Unknown",
        "contact_role": contact_role or "",
        "invoices": invoices,
    }


def extract_recording_link(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """The plain `recordingUrl` field (top-level and `artifact.recordingUrl`) points at a
    private Cloudflare R2 bucket and is never independently fetchable — confirmed against real
    pulled payloads, where it shares the same object key as `artifact.presignedMonoUrl` but
    without the signature query string a private bucket requires. Only the presigned variant
    works, and only until `artifact.presignedUrlsExpiresAt` (~30 minutes after Vapi generated
    it). There is no way to derive a link here that is still good days later — see
    docs/FAILURES.md and cli.py's `fetch-recording`, which re-fetches the call fresh instead of
    reading a stored link.
    """
    artifact = raw.get("artifact", {})
    url = artifact.get("presignedMonoUrl") or raw.get("recordingUrl") or artifact.get("recordingUrl")
    expires_at = artifact.get("presignedUrlsExpiresAt")
    return url, expires_at


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def save_parsed(transcript: Transcript, parsed_dir: Path = PARSED_DIR) -> Path:
    parsed_dir.mkdir(parents=True, exist_ok=True)
    path = parsed_dir / f"{transcript.call_id}.json"
    path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return path
