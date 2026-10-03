"""Demo console Pass 2 — replays a saved fixture call through the real post-call pipeline and
streams it to the browser over SSE.

Two fixed scenarios, both real, on-disk fixture calls (fixtures/raw/*.json) — no synthetic
transcript, no fabricated tool call, no invented specialist output:

- "clean": 01a0edd7-f19a-7000-956e-08f20925f97a, the gpt-4o English control call to a current,
  valid account (ACC-0019). Run through the real pipeline it lands on auto_write.
- "flagged": 01a0a8b0-8c17-7557-a077-16bf9def6431, a real call that genuinely disagrees: the
  outcome specialist says promise_to_pay while the promise validator finds the promised date
  (2026-08-28) was already in the past by the time of the call (2026-09-16) — a real, dated
  promise that should never have been made in good faith, correctly downgraded and queued.

  This call's account ("Rodriguez, Figueroa and Sanchez") no longer exists in the current
  dataset — reassigned when the fake data was regenerated (docs/FAILURES.md, "fixtures coupled
  to generated data went stale silently"). Every *other* real, valid-account call this project
  has on disk now comes back auto_write once two real bugs this replay work surfaced were fixed
  (see openai_client.py's `temperature=0` and promise.py's `_normalize_invoice_ref`) — so rather
  than fabricate an account or leave a bug in place to keep a demo path alive, this scenario is
  wired to a real historical call and labeled as exactly that: the transcript, tool calls, and
  specialist findings are 100% real; the account context is parsed from the same call's own
  embedded prompt facts (not a live Sheet row) and shown with an explicit "historical account"
  note rather than invented current figures.

`as_of` matters: the CLI's `run-postcall` defaults to `date.today()`, which is correct for a
call that just happened but wrong for replaying an old one — the promise validator's
"future-dated" check would spuriously fail against today's date for reasons that have nothing
to do with the call itself. Replay uses the call's own recorded date instead, so the verdict
doesn't depend on which day someone happens to click the button.
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any

from collections_agent.config import get_settings
from collections_agent.models.domain import Invoice, PostCallAnalysis, WriteDecision
from collections_agent.postcall.pipeline import run_postcall
from collections_agent.postcall.transcript import (
    RAW_DIR,
    ToolCallRecord,
    Transcript,
    account_facts_from_system_prompt,
    parse_transcript,
)
from collections_agent.sheets.client import load_accounts_and_invoices

SCENARIOS: dict[str, dict[str, Any]] = {
    "clean": {"call_id": "01a0edd7-f19a-7000-956e-08f20925f97a", "account_id": "ACC-0019", "historical": False},
    "flagged": {"call_id": "01a0a8b0-8c17-7557-a077-16bf9def6431", "account_id": None, "historical": True},
}

# gpt-4o-mini list pricing (verify against platform.openai.com/pricing before trusting this
# months from now — prices move and this file won't know). Applied to real token counts from
# usage_sink, not an estimate.
_INPUT_PRICE_PER_TOKEN = 0.150 / 1_000_000
_OUTPUT_PRICE_PER_TOKEN = 0.600 / 1_000_000

CALL_CAP_SECONDS = 180

_REASON_LABELS = {
    "low_confidence": "low confidence from at least one specialist",
    "disagreement": "two specialists reached findings that can't both be right",
    "compliance_violation": "a compliance violation",
}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _load_raw(call_id: str) -> dict[str, Any]:
    path = RAW_DIR / f"{call_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _scale_delay(real_gap_seconds: float) -> float:
    """Compresses real inter-turn silence into a watchable pace without inventing timing —
    every gap is still proportional to how long the real pause actually was."""
    return max(0.5, min(3.0, real_gap_seconds * 0.35))


def _messages(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return raw.get("messages") or raw.get("artifact", {}).get("messages", [])


def _build_timeline(raw: dict[str, Any], transcript: Transcript) -> list[dict[str, Any]]:
    """Walks the real Vapi message log in order, emitting one item per turn or tool call with a
    delay scaled from the real `secondsFromStart` gap. Tool call name/arguments/result are
    pulled from the already-parsed `transcript.tool_calls` (consumed in order) rather than
    re-parsed here, so there's one parser, not two."""
    timeline: list[dict[str, Any]] = []
    prev_seconds = 0.0
    tool_calls_iter = iter(transcript.tool_calls)

    for m in _messages(raw):
        role = m.get("role")
        seconds = m.get("secondsFromStart")
        gap = (seconds - prev_seconds) if isinstance(seconds, (int, float)) else 1.2

        if role in ("user", "assistant", "bot"):
            content = (m.get("message") or m.get("content") or "").strip()
            if not content:
                continue
            timeline.append(
                {
                    "kind": "turn",
                    "role": "customer" if role == "user" else "agent",
                    "text": content,
                    "seconds_from_start": seconds,
                    "delay": _scale_delay(max(0.0, gap)),
                }
            )
            if isinstance(seconds, (int, float)):
                prev_seconds = seconds
        elif role == "tool_calls":
            for _ in m.get("toolCalls") or []:
                record = next(tool_calls_iter, None)
                if record is None:
                    continue
                timeline.append(
                    {
                        "kind": "tool_call",
                        "record": record,
                        "delay": _scale_delay(max(0.0, gap)),
                    }
                )
            if isinstance(seconds, (int, float)):
                prev_seconds = seconds

    return timeline


def _fmt_money(amount: float) -> str:
    return f"₹{amount:,.0f}"


def _tool_call_payload(record: ToolCallRecord) -> dict[str, Any]:
    args = record.arguments
    result = record.result or {}

    if record.name == "record_ptp":
        amount = args.get("amount")
        method = args.get("method", "")
        promised_date = args.get("date", "")
        invoice = ", ".join(args.get("invoice_ids") or [])
        ptp_id = result.get("ptp_id", "pending")
        amount_str = _fmt_money(amount) if isinstance(amount, (int, float)) else str(amount)
        return {
            "kind": "Promise to pay",
            "fields": f"{amount_str} · {promised_date} · {method} · invoice {invoice}",
            "tool_line": f"record_ptp → {ptp_id}",
            "annotation": f"Promise captured — {amount_str}, {promised_date}, {method}",
        }
    if record.name == "schedule_callback":
        reason = args.get("reason", "")
        priority = args.get("priority", "normal")
        cb_id = result.get("callback_id", "pending")
        return {
            "kind": "Callback scheduled",
            "fields": f"{reason} · priority {priority}",
            "tool_line": f"schedule_callback → {cb_id}",
            "annotation": f"Callback scheduled — {reason}" if reason else "Callback scheduled",
        }
    if record.name == "log_dispute":
        invoice = args.get("invoice_id", "")
        reason_code = args.get("reason_code", "")
        detail = args.get("detail", "")
        dispute_id = result.get("dispute_id", "pending")
        return {
            "kind": "Dispute",
            "fields": f"{reason_code} · {detail} · invoice {invoice}",
            "tool_line": f"log_dispute → {dispute_id}",
            "annotation": f"Dispute logged — {reason_code}",
        }
    if record.name == "send_document":
        invoice = args.get("invoice_id", "")
        doc_type = args.get("doc_type", "")
        return {
            "kind": "Document requested",
            "fields": f"{doc_type} · invoice {invoice}",
            "tool_line": "send_document → queued",
            "annotation": f"Document requested — {doc_type}",
        }
    if record.name == "log_payment_claim":
        invoice = args.get("invoice_id", "")
        reference = args.get("reference", "")
        claim_id = result.get("claim_id", "pending")
        return {
            "kind": "Payment claimed",
            "fields": f"ref {reference} · invoice {invoice}",
            "tool_line": f"log_payment_claim → {claim_id}",
            "annotation": "Customer claims this invoice is already paid",
        }
    return {
        "kind": record.name,
        "fields": json.dumps(args),
        "tool_line": f"{record.name} → {json.dumps(result)}",
        "annotation": f"{record.name} called",
    }


def _account_context(
    customer_name: str,
    contact_name: str,
    contact_role: str,
    invoices: list[Invoice],
    promise_invoice_ids: list[str],
    as_of: date,
    historical: bool,
) -> dict[str, Any]:
    primary = next(
        (inv for inv in invoices if inv.invoice_id in promise_invoice_ids or inv.invoice_number in promise_invoice_ids),
        None,
    )
    if primary is None and invoices:
        primary = invoices[0]
    deferred = next((inv for inv in invoices if primary is None or inv.invoice_number != primary.invoice_number), None)

    def _invoice_payload(inv: Invoice | None) -> dict[str, Any] | None:
        if inv is None:
            return None
        return {
            "invoice_number": inv.invoice_number,
            "amount": inv.outstanding(),
            "due_date": inv.due_date.isoformat(),
            "aging_bucket": inv.aging_bucket(as_of).value,
            "days_overdue": (as_of - inv.due_date).days,
        }

    return {
        "customer_name": customer_name,
        "contact_name": contact_name,
        "contact_role": contact_role,
        "primary_invoice": _invoice_payload(primary),
        "deferred_invoice": _invoice_payload(deferred),
        "historical": historical,
        "historical_note": (
            "This account no longer exists in the current dataset — it was reassigned when the "
            "underlying fake data was regenerated. These invoice facts are the real ones the "
            "agent was given for this real call, read from the call's own record, not a live "
            "Sheet row."
        )
        if historical
        else None,
    }


def _plain_reason(exception_reason: str) -> str:
    categories = [c for c in exception_reason.split(",") if c]
    labels = [_REASON_LABELS.get(c, c) for c in categories]
    if not labels:
        return "review needed"
    return " and ".join(labels)


def _decision_note(analysis: PostCallAnalysis, confidence_threshold: float) -> str:
    categories = analysis.exception_reason.split(",") if analysis.exception_reason else []
    if "compliance_violation" in categories:
        return (
            "A hard compliance violation overrides confidence by design — this holds "
            "regardless of how confident every specialist felt."
        )
    if "disagreement" in categories:
        return (
            f"Overall confidence was {analysis.overall_confidence:.2f}, but two specialists "
            "reached findings that can't both be right — a supervisor catches that; a "
            "lone confidence score wouldn't."
        )
    if "low_confidence" in categories:
        return (
            f"Overall confidence {analysis.overall_confidence:.2f} fell below the "
            f"{confidence_threshold:.2f} threshold this system requires before writing "
            "automatically."
        )
    return ""


def _sheet_rows_preview(analysis: PostCallAnalysis, transcript: Transcript) -> list[dict[str, str]]:
    """Mirrors postcall/writeback.py's real gating logic and row shape exactly, computed from
    the real PostCallAnalysis — this pass doesn't call Sheets (no demo sheet exists yet per
    docs/DEMO_UI_SPEC.md section 9), but the row contents are real, not invented."""
    rows = [
        {
            "tab": "Call_Log",
            "fields": (
                f"outcome: {analysis.outcome.outcome.value} · "
                f"qa_score: {analysis.compliance.qa_score:.2f} · "
                f"duration: {round(transcript.duration_seconds or 0)}s"
            ),
        }
    ]
    if analysis.write_decision == WriteDecision.EXCEPTION_QUEUE:
        rows.append(
            {
                "tab": "Exceptions",
                "fields": f"reason: {analysis.exception_reason} · notes: {analysis.supervisor_notes}",
            }
        )
        return rows

    promise = analysis.promise
    if promise.has_promise and not promise.downgraded_to_soft_commitment:
        rows.append(
            {
                "tab": "PTP_Register",
                "fields": (
                    f"PTP-{analysis.call_id} · {', '.join(promise.invoice_ids)} · "
                    f"{_fmt_money(promise.amount or 0)} · {promise.promised_date} · "
                    f"{promise.method.value if promise.method else ''} · open"
                ),
            }
        )
    elif promise.has_promise and promise.downgraded_to_soft_commitment:
        rows.append(
            {
                "tab": "Soft_Commitments",
                "fields": f"SC-{analysis.call_id} · {', '.join(promise.invoice_ids)} · {promise.notes}",
            }
        )

    if analysis.dispute.has_dispute:
        rows.append(
            {
                "tab": "Disputes",
                "fields": (
                    f"DSP-{analysis.call_id} · invoice {analysis.dispute.invoice_id} · "
                    f"{analysis.dispute.reason_code.value if analysis.dispute.reason_code else ''} · "
                    f"routed to {analysis.dispute.routing_target.value if analysis.dispute.routing_target else 'ops'}"
                ),
            }
        )
    return rows


def _specialist_payload(name: str, result: Any) -> dict[str, Any]:
    if name == "outcome":
        return {
            "name": "outcome",
            "confidence": result.confidence,
            "violation": False,
            "facts": [
                ["Type", result.outcome.value],
                ["Reason code", result.reason_code.value if result.reason_code else "none"],
                ["Next action", result.next_action],
            ],
        }
    if name == "promise":
        return {
            "name": "promise",
            "confidence": result.confidence,
            "violation": False,
            "facts": [
                ["Has promise", "yes" if result.has_promise else "no"],
                ["Complete", "yes" if result.is_complete else "no"],
                ["Future-dated", "yes" if result.is_future_dated else "no"],
                ["Within outstanding", "yes" if result.amount_within_outstanding else "no"],
                ["Method valid", "yes" if result.method_valid else "no"],
                ["Downgraded", "yes" if result.downgraded_to_soft_commitment else "no"],
            ],
        }
    if name == "dispute":
        facts = [["Found", "yes" if result.has_dispute else "none"]]
        if result.has_dispute and result.reason_code:
            facts.append(["Reason", result.reason_code.value])
        return {"name": "dispute", "confidence": result.confidence, "violation": False, "facts": facts}
    if name == "compliance":
        violation = (
            not result.verified_authority
            or not result.stayed_within_permitted_facts
            or result.promised_discount_or_waiver
            or result.threatened_consequences
            or result.misstated_total
        )
        return {
            "name": "compliance",
            "confidence": result.confidence,
            "violation": violation,
            "facts": [
                ["Disclosed automated", "yes" if result.disclosed_automated else "no"],
                ["Authority verified", "yes" if result.verified_authority else "no — hard violation"],
                ["Within permitted facts", "yes" if result.stayed_within_permitted_facts else "no — hard violation"],
                ["Discount offered", "yes — hard violation" if result.promised_discount_or_waiver else "no"],
                ["Consequences threatened", "yes — hard violation" if result.threatened_consequences else "no"],
                ["Totals consistent", "no — hard violation" if result.misstated_total else "yes"],
                ["qa_score", f"{result.qa_score:.2f}"],
            ],
        }
    # summary
    return {"name": "summary", "confidence": None, "violation": False, "text": result.summary}


async def replay_stream(scenario_key: str) -> AsyncGenerator[bytes, None]:
    scenario = SCENARIOS[scenario_key]
    call_id = scenario["call_id"]
    historical = scenario["historical"]

    settings = get_settings()
    raw = _load_raw(call_id)
    transcript = parse_transcript(raw)
    timeline = _build_timeline(raw, transcript)

    if historical:
        facts = account_facts_from_system_prompt(raw)
        account_id = "ACC-HISTORICAL"
        customer_name, contact_name, contact_role = (
            facts["customer_name"],
            facts["contact_name"],
            facts["contact_role"],
        )
        invoices = facts["invoices"]
    else:
        account_id = scenario["account_id"]
        accounts, all_invoices = load_accounts_and_invoices(settings)
        account = next(a for a in accounts if a.account_id == account_id)
        customer_name, contact_name, contact_role = (
            account.customer_name,
            account.contact_name,
            account.contact_role,
        )
        invoices = [inv for inv in all_invoices if inv.account_id == account_id]

    as_of = transcript.started_at.date() if transcript.started_at else date.today()

    promise_invoice_ids: list[str] = []
    for record in transcript.tool_calls:
        if record.name == "record_ptp":
            promise_invoice_ids = record.arguments.get("invoice_ids") or []
            break

    yield _sse(
        "meta",
        {
            "scenario": scenario_key,
            "call_id": call_id,
            "account": _account_context(
                customer_name, contact_name, contact_role, invoices, promise_invoice_ids, as_of, historical
            ),
            "call_cap_seconds": CALL_CAP_SECONDS,
        },
    )

    for item in timeline:
        await asyncio.sleep(item["delay"])
        if item["kind"] == "turn":
            yield _sse(
                "turn",
                {
                    "role": item["role"],
                    "text": item["text"],
                    "seconds_from_start": item["seconds_from_start"],
                },
            )
        else:
            yield _sse("tool_call", _tool_call_payload(item["record"]))

    duration = round(transcript.duration_seconds or 0)
    yield _sse(
        "call_ended",
        {
            "duration_seconds": duration,
            "ended_reason": transcript.ended_reason,
            "exceeded_cap": duration >= CALL_CAP_SECONDS,
            "voice_cost": transcript.cost_usd,
        },
    )

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def on_specialist_done(name: str, result: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (name, result))

    usage_sink: list[Any] = []
    future = loop.run_in_executor(
        None,
        functools.partial(
            run_postcall,
            call_id=call_id,
            account_id=account_id,
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
        name, result = await queue.get()
        received += 1
        yield _sse("specialist", _specialist_payload(name, result))

    analysis: PostCallAnalysis = await future

    input_tokens = sum(getattr(u, "prompt_tokens", 0) for u in usage_sink)
    output_tokens = sum(getattr(u, "completion_tokens", 0) for u in usage_sink)
    pipeline_cost = input_tokens * _INPUT_PRICE_PER_TOKEN + output_tokens * _OUTPUT_PRICE_PER_TOKEN
    voice_cost = transcript.cost_usd or 0.0
    total_cost = voice_cost + pipeline_cost

    yield _sse(
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
