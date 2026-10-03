"""Demo console Pass 3 — wires an actual live Vapi call into the demo page.

Single-caller, local-testing scope only (see `docs/DEMO_UI_SPEC.md` section 9 — per-visitor
accounts, hard caps, and a separate demo sheet are deployment work, deliberately not built yet).
There is exactly one "current live call" channel here, not one keyed per call_id: Vapi's real
tool-calls webhook message carries no call identifier at all — verified against real captured
payloads in `fixtures/webhook_requests.jsonl`, where `tool-calls` messages have no `call` key at
all, unlike `end-of-call-report`/`status-update`, which do. For one local tester dialing one call
at a time, a single shared channel is exactly as correct as a keyed one and far simpler; this
would need revisiting (real per-call correlation, most likely by having the browser mint and pass
its own call-scoped token) before any multi-visitor deployment.

The `end-of-call-report` webhook message already contains the full final call record —
`messages`, `startedAt`, `endedAt`, `cost`, `endedReason` — the exact shape `parse_transcript`
already expects from `fixtures/raw/*.json` (verified against the same real captured payloads).
So finishing a live call needs no separate REST round-trip back to Vapi (`VapiClient.get_call`,
used by `pull-transcripts`): the webhook body itself already is the raw record, and using it
directly avoids the "transcript not ready yet" race a REST fetch immediately after hang-up
would risk.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import date
from typing import Any

from collections_agent.config import get_settings
from collections_agent.models.domain import PostCallAnalysis
from collections_agent.postcall.pipeline import run_postcall
from collections_agent.postcall.transcript import ToolCallRecord, Transcript, parse_transcript
from collections_agent.precall.context_pack import build_context_pack
from collections_agent.sheets.client import load_accounts_and_invoices
from collections_agent.voice.assistant_config import build_call_overrides
from collections_agent.webhooks.demo_replay import (
    CALL_CAP_SECONDS,
    _INPUT_PRICE_PER_TOKEN,
    _OUTPUT_PRICE_PER_TOKEN,
    _account_context,
    _decision_note,
    _plain_reason,
    _sheet_rows_preview,
    _specialist_payload,
    _sse,
    _tool_call_payload,
)

# The one fixed demo account Pass 3 dials against locally — the same account/invoice story as
# Pass 2's "clean" scenario, so all three passes are demonstrating the same customer.
DEMO_ACCOUNT_ID = "ACC-0019"

_queue: asyncio.Queue[bytes] = asyncio.Queue()
_history: list[bytes] = []


def _reset_channel() -> None:
    global _queue, _history
    _queue = asyncio.Queue()
    _history = []


def _push(event: bytes) -> None:
    _history.append(event)
    _queue.put_nowait(event)


def _load_demo_account_and_invoices(settings: Any) -> tuple[Any, list[Any]]:
    accounts, all_invoices = load_accounts_and_invoices(settings)
    account = next(a for a in accounts if a.account_id == DEMO_ACCOUNT_ID)
    invoices = [inv for inv in all_invoices if inv.account_id == DEMO_ACCOUNT_ID]
    return account, invoices


def build_live_call_config() -> dict[str, Any]:
    """Called when the demo page's Start button is clicked. Resets the live channel (a fresh
    call is about to start) and returns everything the browser needs: the public key and
    assistant id for the Vapi Web SDK, the real per-call assistantOverrides (the actual rendered
    system prompt for the demo account — the same builder real dialing uses), and the account
    context to show immediately, before the call connects (spec 3a)."""
    _reset_channel()
    settings = get_settings()
    account, invoices = _load_demo_account_and_invoices(settings)
    as_of = date.today()
    context_pack = build_context_pack(
        account, invoices, ptp_history=[], open_disputes=[], call_log=[], as_of=as_of
    )
    overrides = build_call_overrides(
        context_pack, settings.company_name, settings.vapi_openai_credential_id or None, current_date=as_of
    )
    account_ctx = _account_context(
        account.customer_name,
        account.contact_name,
        account.contact_role,
        invoices,
        [],  # which invoice gets promised isn't known until the call happens
        as_of,
        False,
    )
    return {
        "publicKey": settings.vapi_public_key,
        "assistantId": settings.vapi_assistant_id,
        "assistantOverrides": overrides,
        "account": account_ctx,
    }


def record_tool_call(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
    """Called by POST /vapi/tool-calls right after a real tool dispatch — pushes a real
    captured-card/annotation event to whatever demo page is currently listening on the live
    channel. This is the "tool calls arriving from the webhook as they fire" half of Pass 3."""
    record = ToolCallRecord(name=name, arguments=arguments, result=result)
    _push(_sse("tool_call", _tool_call_payload(record)))


async def handle_end_of_call(message: dict[str, Any]) -> None:
    """Called from POST /vapi/events on a real end-of-call-report — runs the actual post-call
    pipeline against the call that just happened and streams it through the live channel
    exactly like replay mode streams a fixture, using the same event names and shapes."""
    call_id = (message.get("call") or {}).get("id") or "unknown-live-call"
    raw = {**message, "id": call_id}
    transcript: Transcript = parse_transcript(raw)

    settings = get_settings()
    _, invoices = _load_demo_account_and_invoices(settings)
    as_of = date.today()

    duration = round(transcript.duration_seconds or 0)
    _push(
        _sse(
            "call_ended",
            {
                "duration_seconds": duration,
                "ended_reason": transcript.ended_reason,
                "exceeded_cap": duration >= CALL_CAP_SECONDS,
                "voice_cost": transcript.cost_usd,
            },
        )
    )

    loop = asyncio.get_running_loop()
    specialist_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def on_specialist_done(name: str, result: Any) -> None:
        loop.call_soon_threadsafe(specialist_queue.put_nowait, (name, result))

    usage_sink: list[Any] = []
    future = loop.run_in_executor(
        None,
        functools.partial(
            run_postcall,
            call_id=call_id,
            account_id=DEMO_ACCOUNT_ID,
            transcript=transcript,
            invoices=invoices,
            as_of=as_of,
            api_key=settings.openai_api_key,
            confidence_threshold=settings.confidence_threshold,
            on_specialist_done=on_specialist_done,
            usage_sink=usage_sink,
        ),
    )

    received = 0
    while received < 5:
        name, result = await specialist_queue.get()
        received += 1
        _push(_sse("specialist", _specialist_payload(name, result)))

    analysis: PostCallAnalysis = await future

    input_tokens = sum(getattr(u, "prompt_tokens", 0) for u in usage_sink)
    output_tokens = sum(getattr(u, "completion_tokens", 0) for u in usage_sink)
    pipeline_cost = input_tokens * _INPUT_PRICE_PER_TOKEN + output_tokens * _OUTPUT_PRICE_PER_TOKEN
    voice_cost = transcript.cost_usd or 0.0
    total_cost = voice_cost + pipeline_cost

    _push(
        _sse(
            "final",
            {
                "write_decision": analysis.write_decision.value,
                "overall_confidence": analysis.overall_confidence,
                "reason_label": _plain_reason(analysis.exception_reason) if analysis.exception_reason else None,
                "supervisor_notes": analysis.supervisor_notes,
                "decision_note": _decision_note(analysis, settings.confidence_threshold),
                "sheet_rows": _sheet_rows_preview(analysis, transcript),
                "cost": {
                    "duration_seconds": duration,
                    "voice_cost": voice_cost,
                    "pipeline_cost": pipeline_cost,
                    "total_cost": total_cost,
                    "monthly_cost": total_cost * 1000,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            },
        )
    )


async def live_stream():
    for event in list(_history):
        yield event
    while True:
        event = await _queue.get()
        yield event
        if event.startswith(b"event: final"):
            break
