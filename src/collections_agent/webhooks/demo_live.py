"""Demo console Pass 3 — wires an actual live Vapi call into the demo page, with server-side
caps so it's safe to put in front of a recruiter against a small, finite Vapi balance.

Single-caller, local-traffic scope for the SSE channel itself (see below) — per-visitor and
daily caps, and cumulative-spend tracking, are built; true concurrent calls from different
visitors are not (see docs/SHIP_PLAN.md for why that's an accepted limitation, not fixed here).

There is exactly one "current live call" channel, not one keyed per call_id: Vapi's real
tool-calls webhook message carries no call identifier at all — verified against real captured
payloads in `fixtures/webhook_requests.jsonl`, where `tool-calls` messages have no `call` key at
all, unlike `end-of-call-report`/`status-update`, which do. For the traffic level this demo is
built for (a personal portfolio link, a handful of calls a day at most), a single shared
channel is far simpler than per-call correlation and the caps below make genuinely overlapping
calls unlikely; this would need revisiting before any larger-scale deployment.

The `end-of-call-report` webhook message already contains the full final call record —
`messages`, `startedAt`, `endedAt`, `cost`, `endedReason` — the exact shape `parse_transcript`
already expects from `fixtures/raw/*.json` (verified against the same real captured payloads).
So finishing a live call needs no separate REST round-trip back to Vapi (`VapiClient.get_call`,
used by `pull-transcripts`): the webhook body itself already is the raw record.

The account/invoices dialed are a frozen fixture (`demo_fixtures.py`), not a live Sheets read —
the public page touches zero external services besides OpenAI and Vapi at runtime.
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from datetime import date
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from collections_agent.config import Settings, get_settings
from collections_agent.models.domain import PostCallAnalysis
from collections_agent.postcall.pipeline import run_postcall
from collections_agent.postcall.transcript import ToolCallRecord, Transcript, parse_transcript
from collections_agent.precall.context_pack import build_context_pack
from collections_agent.voice.assistant_config import build_call_overrides
from collections_agent.webhooks import demo_caps
from collections_agent.webhooks.demo_fixtures import DEMO_ACCOUNT, DEMO_ACCOUNT_ID, DEMO_INVOICES
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

VISITOR_COOKIE_NAME = "demo_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~400 days — browsers cap cookie lifetime near this

_queue: asyncio.Queue[bytes] = asyncio.Queue()
_history: list[bytes] = []


def _reset_channel() -> None:
    global _queue, _history
    _queue = asyncio.Queue()
    _history = []


def _push(event: bytes) -> None:
    _history.append(event)
    _queue.put_nowait(event)


def _demo_account_context() -> dict[str, Any]:
    return _account_context(
        DEMO_ACCOUNT.customer_name,
        DEMO_ACCOUNT.contact_name,
        DEMO_ACCOUNT.contact_role,
        DEMO_INVOICES,
        [],  # which invoice gets promised isn't known until the call happens
        date.today(),
        False,
    )


_REASON_MESSAGES = {
    "spend_floor": "Live calling is paused — the demo's call budget is below its safety floor.",
    "daily_ceiling": "Live calling has hit today's call limit. It resets tomorrow.",
    "visitor_window": "You've already used your live call for today — one per visitor, so "
    "everyone gets a turn. It resets 24 hours after your last call.",
}


def _check_availability(settings: Settings, visitor_id: str | None) -> dict[str, Any]:
    """The single source of truth both /demo/live/config and /demo/live/status read from —
    never lets the browser see a working assistantOverrides unless every cap clears."""
    remaining_budget = settings.demo_budget_usd - demo_caps.cumulative_spend(settings.demo_state_db_path)
    daily_used = demo_caps.calls_today_count(settings.demo_state_db_path)
    remaining_today = max(0, settings.demo_daily_ceiling - daily_used)

    reason: str | None = None
    if remaining_budget < settings.demo_spend_floor_usd:
        reason = "spend_floor"
    elif daily_used >= settings.demo_daily_ceiling:
        reason = "daily_ceiling"
    elif visitor_id and (
        demo_caps.visitor_call_count(settings.demo_state_db_path, visitor_id, settings.demo_visitor_window_hours)
        >= settings.demo_calls_per_visitor_window
    ):
        reason = "visitor_window"

    return {
        "available": reason is None,
        "reason": reason,
        "message": _REASON_MESSAGES.get(reason) if reason else None,
        "remaining_today": remaining_today,
        "daily_ceiling": settings.demo_daily_ceiling,
    }


async def live_status(request: Request) -> JSONResponse:
    """Read-only — called on page load so the persona briefing and any low-allowance note
    render before the visitor ever clicks Start. Never mints a visitor cookie; a visitor who
    hasn't clicked Start yet has no cookie, and that's fine — nothing to count yet."""
    settings = get_settings()
    visitor_id = request.cookies.get(VISITOR_COOKIE_NAME)
    availability = _check_availability(settings, visitor_id)
    return JSONResponse({**availability, "account": _demo_account_context()})


async def build_live_call_config(request: Request) -> JSONResponse:
    """Called when the demo page's Start button is clicked, after the mic pre-check passes.
    Mints a visitor cookie on first use. Returns either a working Vapi config (caps clear) or
    an `available: false` payload the frontend falls back to replay mode on — the browser
    never receives `publicKey`/`assistantOverrides` for a call that isn't actually allowed."""
    settings = get_settings()
    visitor_id = request.cookies.get(VISITOR_COOKIE_NAME)
    is_new_visitor = visitor_id is None
    if is_new_visitor:
        visitor_id = str(uuid.uuid4())

    availability = _check_availability(settings, visitor_id)
    account_ctx = _demo_account_context()

    if availability["available"]:
        _reset_channel()
        as_of = date.today()
        context_pack = build_context_pack(
            DEMO_ACCOUNT, DEMO_INVOICES, ptp_history=[], open_disputes=[], call_log=[], as_of=as_of
        )
        overrides = build_call_overrides(
            context_pack, settings.company_name, settings.vapi_openai_credential_id or None, current_date=as_of
        )
        demo_caps.record_call_start(settings.demo_state_db_path, visitor_id)
        payload = {
            **availability,
            "publicKey": settings.vapi_public_key,
            "assistantId": settings.vapi_assistant_id,
            "assistantOverrides": overrides,
            "account": account_ctx,
        }
    else:
        payload = {**availability, "account": account_ctx}

    response = JSONResponse(payload)
    if is_new_visitor:
        response.set_cookie(
            VISITOR_COOKIE_NAME, visitor_id, max_age=VISITOR_COOKIE_MAX_AGE, httponly=True, samesite="lax"
        )
    return response


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
    settings = get_settings()
    call_id = (message.get("call") or {}).get("id") or "unknown-live-call"
    raw = {**message, "id": call_id}
    transcript: Transcript = parse_transcript(raw)

    if transcript.cost_usd is not None:
        demo_caps.record_call_cost(settings.demo_state_db_path, transcript.cost_usd)

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
            invoices=DEMO_INVOICES,
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
