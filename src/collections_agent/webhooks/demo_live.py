"""Browser voice demo: visitor-owned sessions and authenticated provider events."""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from collections_agent.config import Settings, get_settings
from collections_agent.models.domain import PostCallAnalysis
from collections_agent.postcall.pipeline import run_postcall
from collections_agent.postcall.transcript import ToolCallRecord, Transcript, TranscriptTurn, parse_transcript
from collections_agent.precall.context_pack import build_context_pack
from collections_agent.voice.assistant_config import build_call_overrides
from collections_agent.webhooks import demo_caps
from collections_agent.webhooks import demo_sessions as sessions
from collections_agent.webhooks.demo_fixtures import DEMO_ACCOUNT, DEMO_ACCOUNT_ID, DEMO_INVOICES
from collections_agent.webhooks.demo_replay import (
    _INPUT_PRICE_PER_TOKEN,
    _OUTPUT_PRICE_PER_TOKEN,
    CALL_CAP_SECONDS,
    _account_context,
    _decision_note,
    _plain_reason,
    _sheet_rows_preview,
    _specialist_payload,
    _tool_call_payload,
)

logger = logging.getLogger(__name__)
VISITOR_COOKIE_NAME = "demo_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 400
_REASON_MESSAGES = {
    "configuration": "Live calling is being set up. Please try a replay for now.",
    "busy": "Another demo is in progress. Please try again in a few minutes, or replay a demo.",
    "spend_floor": "Live calling is paused because the demo budget is running low.",
    "daily_ceiling": "Live calling has hit today's call limit. It resets tomorrow (UTC).",
    "visitor_window": "You've used your live call for today. It resets 24 hours after your last call.",
}


def _demo_account_context():
    as_of = datetime.now(UTC).date()
    pack = build_context_pack(DEMO_ACCOUNT, DEMO_INVOICES, [], [], [], as_of=as_of)
    return _account_context(
        DEMO_ACCOUNT.customer_name,
        DEMO_ACCOUNT.contact_name,
        DEMO_ACCOUNT.contact_role,
        DEMO_INVOICES,
        [pack.primary_invoice_id],
        as_of,
        False,
    )


def _check_availability(settings: Settings, visitor_id: str | None, *, check_busy=True):
    db = settings.demo_state_db_path
    used = demo_caps.calls_today_count(db)
    reason = None
    if not settings.demo_live_enabled or not all(
        (
            settings.vapi_public_key,
            settings.vapi_assistant_id,
            settings.vapi_server_secret,
            settings.openai_api_key,
        )
    ):
        reason = "configuration"
    elif settings.demo_budget_usd - demo_caps.cumulative_spend(db) < settings.demo_spend_floor_usd:
        reason = "spend_floor"
    elif used >= settings.demo_daily_ceiling:
        reason = "daily_ceiling"
    elif (
        visitor_id
        and demo_caps.visitor_call_count(db, visitor_id, settings.demo_visitor_window_hours)
        >= settings.demo_calls_per_visitor_window
    ):
        reason = "visitor_window"
    elif check_busy and sessions.busy(db):
        reason = "busy"
    return {
        "available": reason is None,
        "reason": reason,
        "message": _REASON_MESSAGES.get(reason),
        "remaining_today": max(0, settings.demo_daily_ceiling - used),
        "daily_ceiling": settings.demo_daily_ceiling,
    }


def require_same_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        # Render terminates TLS before forwarding to uvicorn. Compare the browser's
        # destination host, without depending on the internal HTTP scheme.
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin demo requests are not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-origin demo requests are not allowed")


def owned_session(request: Request, session_id: str):
    session = sessions.get(get_settings().demo_state_db_path, session_id)
    if not session or session["visitor_id"] != request.cookies.get(VISITOR_COOKIE_NAME):
        raise HTTPException(404, "Live session not found")
    return session


async def live_status(request: Request):
    return JSONResponse(
        {
            **_check_availability(get_settings(), request.cookies.get(VISITOR_COOKIE_NAME)),
            "account": _demo_account_context(),
        },
        headers={"Cache-Control": "no-store"},
    )


async def build_live_call_config(request: Request):
    require_same_origin(request)
    settings = get_settings()
    visitor_id = request.cookies.get(VISITOR_COOKIE_NAME) or str(uuid.uuid4())
    availability = _check_availability(settings, visitor_id)
    payload = {**availability, "account": _demo_account_context()}
    if availability["available"]:
        session_id = sessions.reserve(settings.demo_state_db_path, visitor_id)
        if session_id is None:
            payload.update(available=False, reason="busy", message=_REASON_MESSAGES["busy"])
        else:
            # Re-check caps after taking the exclusive reservation: a previous call may
            # have completed between the initial availability read and this reservation.
            locked_availability = _check_availability(settings, visitor_id, check_busy=False)
            if not locked_availability["available"]:
                sessions.cancel(settings.demo_state_db_path, session_id)
                return JSONResponse(
                    {**locked_availability, "account": _demo_account_context()},
                    headers={"Cache-Control": "no-store"},
                )
            as_of = datetime.now(UTC).date()
            pack = build_context_pack(DEMO_ACCOUNT, DEMO_INVOICES, [], [], [], as_of=as_of)
            overrides = build_call_overrides(
                pack, settings.company_name, settings.vapi_openai_credential_id or None, current_date=as_of
            )
            overrides["variableValues"] = {"demo_session_id": session_id}
            overrides["maxDurationSeconds"] = CALL_CAP_SECONDS
            payload.update(
                publicKey=settings.vapi_public_key,
                assistantId=settings.vapi_assistant_id,
                assistantOverrides=overrides,
                sessionId=session_id,
            )
    response = JSONResponse(payload, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        VISITOR_COOKIE_NAME,
        visitor_id,
        max_age=VISITOR_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
    )
    return response


async def session_action(request: Request, session_id: str, action: str):
    require_same_origin(request)
    session = owned_session(request, session_id)
    db = get_settings().demo_state_db_path
    if action == "connected":
        body = await request.json()
        try:
            call_id = str(uuid.UUID(body.get("callId", "")))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, "Invalid call ID") from None
        if not sessions.connected(db, session_id, call_id):
            raise HTTPException(409, "Session is no longer available")
    elif action == "cancel":
        sessions.cancel(db, session_id)
    elif action == "ended":
        body = await request.json()
        if session["state"] not in ("active", "analyzing") or body.get("callId") != session["call_id"]:
            raise HTTPException(409, "Call has not been connected")
        raw_turns = body.get("turns")
        if not isinstance(raw_turns, list) or len(raw_turns) > 100:
            raise HTTPException(422, "Invalid transcript")
        turns = []
        for turn in raw_turns:
            if not isinstance(turn, dict) or turn.get("role") not in ("user", "bot"):
                raise HTTPException(422, "Invalid transcript turn")
            content = turn.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                raise HTTPException(422, "Invalid transcript turn")
            turns.append(TranscriptTurn(role=turn["role"], content=content))
        duration = body.get("durationSeconds")
        if not isinstance(duration, (int, float)) or not 0 <= duration <= 420:
            raise HTTPException(422, "Invalid call duration")
        # The provider's signed report is authoritative. If it never arrives, the
        # visitor-owned live transcript still lets this synthetic demo finish visibly.
        asyncio.create_task(_recover_ended_call(db, session_id, session["call_id"], turns, duration))
    else:
        raise HTTPException(404)
    return {"status": "ok"}


async def _recover_ended_call(db, session_id, call_id, turns, duration):
    await asyncio.sleep(12)
    try:
        session = sessions.get(db, session_id)
        if not session or session["state"] != "active" or session["call_id"] != call_id:
            return
        logger.warning("Provider report did not arrive for live call %s; using browser transcript", call_id)
        transcript = Transcript(
            call_id=call_id,
            turns=turns,
            tool_calls=[],
            started_at=datetime.now(UTC) - timedelta(seconds=duration),
            duration_seconds=duration,
            ended_reason="customer-ended-call",
        )
        await _finish_analysis(get_settings(), db, session_id, transcript)
    except Exception:
        logger.exception("Live call recovery failed")
        sessions.push(
            db,
            session_id,
            "session_error",
            {"message": "The call ended, but analysis could not finish. Your transcript is still shown."},
        )


def record_tool_call(session_id: str, name: str, arguments: dict, result: dict):
    record = ToolCallRecord(name=name, arguments=arguments, result=result)
    sessions.push(get_settings().demo_state_db_path, session_id, "tool_call", _tool_call_payload(record))


async def handle_end_of_call(message: dict[str, Any]):
    settings = get_settings()
    db = settings.demo_state_db_path
    session_id = sessions.correlate(db, message)
    if not session_id:
        return
    call = message.get("call") or {}
    call_id = call["id"]
    raw = {**call, **message, "id": call_id}
    transcript = parse_transcript(raw)
    # Provider timestamps also confirm calls if the browser closed before its acknowledgement.
    if transcript.started_at or transcript.turns:
        sessions.connected(db, session_id, call_id)
    await _finish_analysis(settings, db, session_id, transcript)


async def _finish_analysis(settings, db, session_id, transcript):
    if not sessions.begin_analysis(db, session_id, transcript.call_id, transcript.cost_usd):
        return

    def emit(name, payload):
        sessions.push(db, session_id, name, payload)

    try:
        # Older provider tool messages can omit call identity. Never attach those to the
        # current visitor; recover their captured cards from this identified final report.
        import json

        captured = [json.loads(row[2]) for row in sessions.events(db, session_id, 0) if row[1] == "tool_call"]
        for record in transcript.tool_calls:
            payload = _tool_call_payload(record)
            if payload not in captured:
                emit("tool_call", payload)
                captured.append(payload)
        async with asyncio.timeout(150):
            await _analyze(settings, transcript, emit)
    except Exception:
        logger.exception("Live post-call analysis failed")
        emit(
            "session_error",
            {"message": "The call ended, but analysis could not finish. Your transcript is still shown."},
        )


async def _analyze(settings, transcript, emit):
    call_id = transcript.call_id
    as_of = datetime.now(UTC).date()
    duration = round(transcript.duration_seconds or 0)
    emit(
        "call_ended",
        {
            "duration_seconds": duration,
            "ended_reason": transcript.ended_reason,
            "exceeded_cap": duration >= CALL_CAP_SECONDS,
            "voice_cost": transcript.cost_usd,
        },
    )
    if not transcript.turns:
        raise ValueError("No spoken conversation to analyze")
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
        try:
            name, result = await asyncio.wait_for(specialist_queue.get(), timeout=0.5)
        except TimeoutError:
            if future.done():
                await future  # Propagate failures instead of waiting forever for five callbacks.
                if specialist_queue.empty():
                    break
            continue
        received += 1
        emit("specialist", _specialist_payload(name, result))

    analysis: PostCallAnalysis = await future

    input_tokens = sum(getattr(u, "prompt_tokens", 0) for u in usage_sink)
    output_tokens = sum(getattr(u, "completion_tokens", 0) for u in usage_sink)
    pipeline_cost = input_tokens * _INPUT_PRICE_PER_TOKEN + output_tokens * _OUTPUT_PRICE_PER_TOKEN
    voice_cost = transcript.cost_usd
    total_cost = voice_cost + pipeline_cost if voice_cost is not None else None

    emit(
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
                "monthly_cost": total_cost * 1000 if total_cost is not None else None,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    )


async def live_stream(request: Request, session_id: str):
    db = get_settings().demo_state_db_path
    try:
        last_id = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        last_id = 0
    heartbeat = 0
    while not await request.is_disconnected():
        for event_id, name, payload in sessions.events(db, session_id, last_id):
            yield f"id: {event_id}\nevent: {name}\ndata: {payload}\n\n".encode()
            last_id = event_id
            if name in ("final", "session_error"):
                return
        session = sessions.get(db, session_id)
        if not session or session["state"] in sessions.TERMINAL:
            return
        heartbeat += 1
        if heartbeat % 30 == 0:
            yield b": heartbeat\n\n"
        await asyncio.sleep(0.5)
