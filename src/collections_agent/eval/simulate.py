"""Text-only simulation of the live call, for testing the agent's in-call behaviour without
spending Vapi credit.

Why this exists: the human-label eval (eval/compare.py, docs/eval.md) grades the *post-call*
pipeline on a finished transcript. Nearly every incident in docs/FAILURES.md happened *during*
the call — a tool described aloud but never called, three questions bundled into one turn, raw
digits read instead of the spoken form, the full-or-partial question skipped, the deferred
invoice's callback never scheduled. Until now those were only caught by listening to individual
live calls. This harness replays scenarios against the real agent prompt and tools as plain
text, so the same checks run in minutes and can be re-run after every prompt change.

What is real here: the system prompt (voice/prompt_template.render_system_prompt), the opening
line (voice/language.opening_message), the tool schemas (voice/tool_schemas.ALL_TOOLS), the tool
handlers' own validation (webhooks/handlers.dispatch — e.g. record_ptp's past-date rejection),
and, optionally, the full post-call pipeline (postcall/pipeline.run_postcall).

What is NOT tested here, by construction: anything about audio — speech-to-text errors, how
text-to-speech pronounces amounts and dates, interruptions/barge-in, latency. Those still need
real calls. The customer is also a model following a brief, not a real person, so this measures
behaviour on scripted situations, not real-world accuracy.

Spend is capped: every API call is costed from its real token usage, and the run stops before
any call that could push total spend past `max_usd`.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from collections_agent.eval.sim_scenarios import MaterializedScenario, ToolExpectation
from collections_agent.postcall.transcript import ToolCallRecord, Transcript, TranscriptTurn
from collections_agent.voice.language import opening_message
from collections_agent.voice.prompt_template import render_system_prompt
from collections_agent.voice.speakable import invoice_words_to_number
from collections_agent.voice.tool_schemas import ALL_TOOLS
from collections_agent.webhooks import handlers

SIM_RUNS_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "eval" / "sim_runs"

# USD per 1M tokens: (input, cached input, output). Check https://openai.com/api/pricing before
# trusting these — a model missing from this table is refused rather than run uncapped.
PRICES_PER_MILLION: dict[str, tuple[float, float, float]] = {
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
}

END_TOKEN = "<END_CALL>"
ACTION_TOOLS = {
    "record_ptp",
    "log_dispute",
    "log_payment_claim",
    "send_document",
    "schedule_callback",
    "mark_opt_out",
}

CUSTOMER_SYSTEM_PROMPT = """You are role-playing a person who has just picked up a phone call \
from a supplier's automated accounts assistant. Stay in character for the whole call.

How to speak:
- Like a busy professional on the phone in India, in English. One or two short sentences per \
reply. No lists, no markdown, no stage directions.
- Only use facts from YOUR BRIEF. If asked something your brief doesn't cover, say you don't \
know. Don't volunteer details unless your brief tells you to.
- Never mention being an AI, a model, a simulation, or a brief.

Ending the call:
- When the assistant has wrapped up (thanked you, said goodbye, or said it will end the call), \
reply with a short goodbye and then the exact text <END_CALL>.
- If your goal is done and the assistant keeps going in circles, say goodbye and <END_CALL>.

Dates:
- Today is {today}. Every date in your brief is in {year} or later. If you say a year, it is \
the one in your brief — never an earlier year.
- If the assistant reads a date back, check the day, month and year against your brief. Agree \
if they match; correct it only if something is actually different.

YOUR BRIEF:
{brief}"""


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostMeter:
    """Running spend, computed from each response's real token usage. `ensure_room` is called
    before every request with a deliberately generous estimate, so the cap is enforced before
    money is spent, not noticed after."""

    max_usd: float
    spent_usd: float = 0.0
    requests: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    reserved_usd: float = 0.0
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self):
        if not math.isfinite(self.max_usd) or self.max_usd < 0:
            raise ValueError("max_usd must be finite and nonnegative")

    def reserve(self, model: str, payload: Any, output_limit: int) -> float:
        # Conservative byte bound, including schema/tools and a framing allowance.
        # Prices are local configuration; this is not an account/provider billing limit.
        tokens = 2 * len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) + 4096
        with self._lock:
            self.ensure_room(model, tokens, output_limit)
            price_in, _, price_out = self.prices(model)
            reserved = (tokens * price_in + output_limit * price_out) / 1_000_000
            self.reserved_usd += reserved
            return reserved

    def settle(self, model: str, usage: Any, reserved: float) -> None:
        with self._lock:
            if usage is None:
                raise RuntimeError("Missing usage: cost unknown; reservation retained, stopping run")
            self.record(model, usage)
            self.reserved_usd -= reserved

    @staticmethod
    def prices(model: str) -> tuple[float, float, float]:
        if model not in PRICES_PER_MILLION:
            raise ValueError(
                f"No price on file for model {model!r}; add it to PRICES_PER_MILLION in "
                "eval/simulate.py so the spending cap can be enforced."
            )
        return PRICES_PER_MILLION[model]

    def ensure_room(self, model: str, est_input_tokens: int, est_output_tokens: int = 500) -> None:
        price_in, _, price_out = self.prices(model)
        estimate = (est_input_tokens * price_in + est_output_tokens * price_out) / 1_000_000
        if self.spent_usd + self.reserved_usd + estimate > self.max_usd:
            raise BudgetExceeded(
                f"stopping: spent ${self.spent_usd:.4f}, next request could cost up to "
                f"${estimate:.4f}, cap is ${self.max_usd:.2f}"
            )

    def record(self, model: str, usage: Any) -> float:
        if usage is None:
            raise RuntimeError("Missing usage: cannot treat an unmetered request as free")
        price_in, price_cached, price_out = self.prices(model)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
        cost = (
            (prompt_tokens - cached) * price_in + cached * price_cached + completion_tokens * price_out
        ) / 1_000_000
        self.spent_usd += cost
        self.requests += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + cost
        return cost


def estimate_tokens(payload: Any) -> int:
    """Rough token count for budgeting only — ~3.5 characters per token, rounded up."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return int(len(text) / 3.5) + 50


def openai_tools() -> list[dict[str, Any]]:
    """The live Vapi tool definitions, minus Vapi-only keys (`messages`), in the shape OpenAI's
    chat completions API expects."""
    return [{"type": "function", "function": tool["function"]} for tool in ALL_TOOLS]


# --- Running one conversation -------------------------------------------------------------------


@dataclass
class SimToolCall:
    turn_index: int  # index into SimConversation.turns of the agent turn this call preceded
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class SimConversation:
    scenario_id: str
    turns: list[tuple[str, str]]  # ("bot" | "user", text)
    tool_calls: list[SimToolCall]
    ended_reason: str  # customer_ended | max_turns | budget | error
    error: str | None = None
    cost_usd: float = 0.0

    def agent_turns(self, include_opening: bool = False) -> list[str]:
        texts = [text for role, text in self.turns if role == "bot"]
        return texts if include_opening else texts[1:]


@contextmanager
def _isolated_tool_log(run_dir: Path) -> Iterator[None]:
    """The live handlers append every call to fixtures/tool_calls.jsonl. Point that at this
    run's own folder instead, so simulated calls never mix into the real call log."""
    original = handlers.TOOL_CALL_LOG_PATH
    handlers.TOOL_CALL_LOG_PATH = run_dir / "tool_calls.jsonl"
    try:
        yield
    finally:
        handlers.TOOL_CALL_LOG_PATH = original


def execute_tool(name: str, raw_arguments: str, ms: MaterializedScenario) -> tuple[dict, dict]:
    """Runs one tool call through the real handler where possible. `lookup_invoices` is answered
    from the scenario's own invoices instead of the Google Sheet."""
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}, {"error": "invalid_arguments", "message": "Arguments were not valid JSON."}
    if name == "lookup_invoices":
        return arguments, {
            "account_id": ms.account.account_id,
            "invoices": [
                {
                    "invoice_number": inv.invoice_number,
                    "amount": inv.amount,
                    "outstanding": inv.outstanding(),
                    "due_date": inv.due_date.isoformat(),
                    "status": inv.status.value,
                }
                for inv in ms.invoices
            ],
        }
    try:
        return arguments, handlers.dispatch(name, arguments)
    except handlers.UnknownToolError:
        return arguments, {"error": "unknown_tool", "message": f"No tool named {name}."}
    except (KeyError, TypeError, ValueError) as e:
        return arguments, {"error": "invalid_arguments", "message": str(e)}


def _chat(client: Any, meter: CostMeter, model: str, messages: list[dict], **kwargs: Any) -> Any:
    kwargs["max_completion_tokens"] = 500
    reservation = meter.reserve(model, {"messages": messages, **kwargs}, 500)
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    meter.settle(model, getattr(response, "usage", None), reservation)
    return response.choices[0].message


class BudgetUsageSink(list):
    """Shared reservation meter for concurrent post-call specialists."""

    def __init__(self, meter: CostMeter):
        super().__init__()
        self.meter = meter
        self.local = threading.local()

    def authorize(self, model: str, payload: dict, output_limit: int):
        self.local.model = model
        self.local.reservation = self.meter.reserve(model, payload, output_limit)

    def append(self, usage):
        self.meter.settle(self.local.model, usage, self.local.reservation)
        super().append(usage)


def _assistant_message_dict(message: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in message.tool_calls
        ],
    }


def run_conversation(
    ms: MaterializedScenario,
    client: Any,
    meter: CostMeter,
    run_dir: Path,
    company_name: str,
    agent_model: str = "gpt-4o-mini",
    customer_model: str = "gpt-4o-mini",
    max_agent_turns: int = 14,
    max_tool_rounds: int = 4,
    temperature: float = 0.0,
) -> SimConversation:
    spent_before = meter.spent_usd
    system_prompt = render_system_prompt(ms.context_pack, company_name, current_date=ms.as_of)
    opening = opening_message(ms.context_pack.preferred_language, company_name, ms.context_pack.contact_name)

    agent_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": opening},
    ]
    customer_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": CUSTOMER_SYSTEM_PROMPT.format(
                brief=ms.customer_brief,
                today=ms.as_of.strftime("%A %d %B %Y"),
                year=ms.as_of.year,
            ),
        },
        {"role": "user", "content": opening},
    ]
    convo = SimConversation(
        scenario_id=ms.scenario.id, turns=[("bot", opening)], tool_calls=[], ended_reason="max_turns"
    )
    tools = openai_tools()

    try:
        with _isolated_tool_log(run_dir):
            for _ in range(max_agent_turns):
                # Customer speaks.
                reply = _chat(client, meter, customer_model, customer_messages, temperature=temperature)
                customer_text = (reply.content or "").strip()
                ended = END_TOKEN in customer_text
                customer_text = customer_text.replace(END_TOKEN, "").strip()
                if customer_text:
                    convo.turns.append(("user", customer_text))
                    customer_messages.append({"role": "assistant", "content": customer_text})
                    agent_messages.append({"role": "user", "content": customer_text})
                if ended:
                    convo.ended_reason = "customer_ended"
                    break

                # Agent responds, possibly after one or more rounds of tool calls.
                agent_text = ""
                for _round in range(max_tool_rounds + 1):
                    message = _chat(
                        client, meter, agent_model, agent_messages, tools=tools, temperature=temperature
                    )
                    if not message.tool_calls:
                        agent_text = (message.content or "").strip()
                        break
                    agent_messages.append(_assistant_message_dict(message))
                    for tc in message.tool_calls:
                        arguments, result = execute_tool(tc.function.name, tc.function.arguments, ms)
                        convo.tool_calls.append(
                            SimToolCall(len(convo.turns), tc.function.name, arguments, result)
                        )
                        agent_messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
                        )
                convo.turns.append(("bot", agent_text))
                agent_messages.append({"role": "assistant", "content": agent_text})
                customer_messages.append({"role": "user", "content": agent_text or "(silence)"})
    except BudgetExceeded as e:
        convo.ended_reason, convo.error = "budget", str(e)
    except Exception as e:  # noqa: BLE001 - one broken scenario must not lose the whole run
        convo.ended_reason, convo.error = "error", f"{type(e).__name__}: {e}"

    convo.cost_usd = meter.spent_usd - spent_before
    return convo


# --- Checks -------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    must_pass: bool = True  # False = reported, but doesn't fail the scenario


def _invoice_key(value: Any) -> str:
    """The same normalization the post-call promise matcher uses, so "matches the invoice" here
    means exactly what it means downstream — including resolving the spoken, spelled-out form
    back to the raw identifier."""
    from collections_agent.postcall.specialists.promise import _normalize_invoice_ref

    return _normalize_invoice_ref(str(value or ""))


def _norm_ref(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _as_list(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def _invoice_args(arguments: dict[str, Any]) -> list[str]:
    ids = arguments.get("invoice_ids")
    if isinstance(ids, list):
        return [str(i) for i in ids]
    return [str(arguments["invoice_id"])] if arguments.get("invoice_id") else []


def _mismatches(exp: ToolExpectation, call: SimToolCall, ms: MaterializedScenario) -> list[str]:
    """Every way `call` fails to satisfy `exp`; an empty list means it matches."""
    args, problems = call.arguments, []

    if exp.invoice:
        targets = [ms.primary_invoice] if exp.invoice == "primary" else ms.other_invoices
        wanted = {_invoice_key(i.invoice_number) for i in targets} | {
            _invoice_key(i.invoice_id) for i in targets
        }
        given = {_invoice_key(i) for i in _invoice_args(args)}
        if not given & wanted:
            problems.append(f"invoice {sorted(given) or 'missing'} is not the {exp.invoice} invoice")

    if exp.amount is not None:
        if exp.amount == "full":
            wanted_amount = ms.primary_invoice.outstanding()
        elif exp.amount == "other_full":
            wanted_amount = ms.other_invoices[0].outstanding()
        else:
            wanted_amount = float(exp.amount)
        try:
            given_amount = float(args.get("amount"))
        except (TypeError, ValueError):
            given_amount = None
        if given_amount is None or abs(given_amount - wanted_amount) > 1:
            problems.append(f"amount {args.get('amount')!r}, expected {wanted_amount:,.0f}")

    if exp.date is not None:
        wanted_date = ms.resolved_dates.get(exp.date) or date.fromisoformat(exp.date)
        given_date = args.get("date") or args.get("paid_on")
        if str(given_date) != wanted_date.isoformat():
            problems.append(f"date {given_date!r}, expected {wanted_date.isoformat()}")

    if exp.method and str(args.get("method", "")).lower() != exp.method.lower():
        problems.append(f"method {args.get('method')!r}, expected {exp.method}")
    if exp.reference and _norm_ref(args.get("reference")) != _norm_ref(exp.reference):
        problems.append(f"reference {args.get('reference')!r}, expected {exp.reference}")
    for key in ("reason_code", "priority", "scope"):
        allowed = _as_list(getattr(exp, key))
        if allowed and args.get(key) not in allowed:
            problems.append(f"{key} {args.get(key)!r}, expected one of {allowed}")
    if "error" in call.result:
        problems.append(f"the tool rejected it: {call.result.get('error')}")
    return problems


def _raw_fact_patterns(ms: MaterializedScenario) -> list[str]:
    """Account facts written as digits — exactly what the structural prompt fix of 2026-09-29
    exists to prevent (docs/FAILURES.md). Amounts in Indian and Western grouping, plus each
    invoice number's numeric tail."""
    patterns = set()
    for inv in ms.invoices:
        whole = int(round(inv.outstanding()))
        patterns.update({str(whole), f"{whole:,}", _indian_grouping(whole)})
        tail = re.findall(r"\d{3,}", inv.invoice_number)
        patterns.update(tail)
    return sorted(patterns, key=len, reverse=True)


def _indian_grouping(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


# "I've recorded…", "it has been logged…", "I will log this as a dispute…" — an action stated as
# done (or about to be done by the agent itself). Deliberately narrow: "I'll send the details in
# writing" and "noted" are things the prompt allows the agent to say without a tool.
_CLAIM_RE = re.compile(
    r"\b(?:(?:i\s*(?:'ve|have)|it\s*(?:'s| has| is)|that\s*(?:'s| has| is)|this\s*(?:'s| has| is))"
    r"\s+(?:now\s+|just\s+|already\s+)?(?:been\s+)?(?:recorded|logged|scheduled|registered|raised|marked)"
    r"|i\s*(?:'ll|will)\s+(?:now\s+|just\s+|go\s+ahead\s+and\s+)?(?:record|log|schedule|register|raise|mark)\b)",
    re.IGNORECASE,
)

_ACTION_TOPICS = {
    "schedule_callback": r"callback|call\s*back|follow[- ]?up|call from|team.*(?:contact|call)",
    "record_ptp": r"promise|commitment|payment plan",
    "log_dispute": r"dispute|complaint",
    "log_payment_claim": r"payment claim|payment reference|UTR|already paid",
    "send_document": r"document|invoice cop(?:y|ies)|statement",
    "mark_opt_out": r"opt[- ]?out|stop calling|no (?:more|further) calls|calling list",
}


def _successful(call: SimToolCall) -> bool:
    return (
        bool(call.result)
        and not call.result.get("error")
        and call.result.get("status") not in {"failed", "error", "rejected", "unavailable"}
    )


def _unbacked_claim(text: str, index: int, calls: list[SimToolCall]) -> bool:
    normalized = text.replace("’", "'")
    claim = _CLAIM_RE.search(normalized) or re.search(
        r"\b(?:i(?:'ll| will| have|'ve)|we(?:'ll| will| have|'ve))\s+(?:arrange|send|stop|remove|ensure)"
        r"|\bexpect a call\b|\b(?:has been|is now)\s+(?:sent|scheduled|removed)",
        normalized,
        re.I,
    )
    if not claim:
        return False
    topics = [name for name, pattern in _ACTION_TOPICS.items() if re.search(pattern, normalized, re.I)]
    # An ambiguous completion claim cannot be validated by an unrelated earlier action.
    if not topics:
        return True
    return any(
        not any(c.name == name and c.turn_index <= index and _successful(c) for c in calls) for name in topics
    )


def _word_count_without_facts(text: str, ms: MaterializedScenario) -> int:
    """Words in a turn, not counting the pre-written spoken forms of invoice numbers, amounts
    and dates — "S, G, P, slash, two, six, ..." is 14 words on its own, and reading supplied
    facts aloud is what the prompt's 25-word rule explicitly allows."""
    from collections_agent.voice.speakable import amount_to_words, date_to_words, invoice_number_to_words

    for inv in ms.invoices:
        for fact in (
            invoice_number_to_words(inv.invoice_number),
            amount_to_words(inv.outstanding()),
            date_to_words(inv.due_date),
        ):
            text = re.sub(re.escape(fact), " ", text, flags=re.IGNORECASE)
    return len(text.split())


def run_checks(ms: MaterializedScenario, convo: SimConversation) -> list[Check]:
    checks: list[Check] = []
    exp = ms.scenario.expect
    unmatched = list(convo.tool_calls)

    # 1. Every expected tool call happened, with the right details.
    for e in exp.tools:
        candidates = [c for c in unmatched if c.name == e.name]
        problems_by_call = [
            (c, _mismatches(e, c, ms) + ([] if _successful(c) else ["tool did not succeed"]))
            for c in candidates
        ]
        match = next((c for c, problems in problems_by_call if not problems), None)
        if match is not None:
            unmatched.remove(match)
            checks.append(Check(f"called {e.name}", True))
        elif candidates:
            detail = "; ".join(problems_by_call[-1][1])
            checks.append(Check(f"called {e.name}", False, f"called, but {detail}"))
        else:
            checks.append(Check(f"called {e.name}", False, "never called"))

    # 2. Forbidden tools never fired.
    fired = {c.name for c in convo.tool_calls}
    for name in exp.forbid_tools:
        checks.append(Check(f"did not call {name}", name not in fired, "" if name not in fired else "called"))

    # 3. Never promised against an invoice that isn't this call's primary one.
    if ms.other_invoices:
        others = {_invoice_key(i.invoice_number) for i in ms.other_invoices}
        bad = [
            c
            for c in convo.tool_calls
            if c.name == "record_ptp" and others & {_invoice_key(i) for i in _invoice_args(c.arguments)}
        ]
        checks.append(
            Check(
                "only worked the primary invoice", not bad, "record_ptp on a deferred invoice" if bad else ""
            )
        )

    # 4. Claimed an action in speech without ever calling a tool for it (the "narrated, never
    #    called" failure from docs/FAILURES.md, 2026-09-16 and 2026-09-26).
    claims = []
    for index, (role, text) in enumerate(convo.turns):
        if role != "bot":
            continue
        if _unbacked_claim(text, index, convo.tool_calls):
            claims.append(text[:120])
    checks.append(Check("no action claimed without a tool call", not claims, " | ".join(claims)))

    agent_turns = convo.agent_turns(include_opening=True)

    # 5. Account facts read out as raw digits instead of the spoken form.
    patterns = _raw_fact_patterns(ms)
    raw_hits = sorted({p for t in agent_turns for p in patterns if p in t})
    checks.append(Check("no account facts spoken as digits", not raw_hits, ", ".join(raw_hits)))

    # 6. Wrong person: no amount or invoice detail may be disclosed.
    if ms.scenario.wrong_person:
        from collections_agent.voice.speakable import amount_to_words, invoice_number_to_words

        secrets = [amount_to_words(i.outstanding()) for i in ms.invoices]
        secrets += [invoice_number_to_words(i.invoice_number) for i in ms.invoices]
        secrets += patterns
        leaked = [s for s in secrets if any(s.lower() in t.lower() for t in agent_turns)]
        checks.append(Check("disclosed nothing to the wrong person", not leaked, ", ".join(leaked[:3])))

    # 7. The call finished.
    checks.append(
        Check(
            "call finished",
            convo.ended_reason == "customer_ended",
            convo.error or ("" if convo.ended_reason == "customer_ended" else convo.ended_reason),
        )
    )

    # Reported, not gating — the invoice argument was the spoken form, which the post-call
    # matcher now resolves in code, but which any other consumer of tool arguments would not.
    spoken_args = [
        ref for c in convo.tool_calls for ref in _invoice_args(c.arguments) if invoice_words_to_number(ref)
    ]
    checks.append(
        Check(
            "invoice arguments use the raw identifier",
            not spoken_args,
            f"{len(spoken_args)} spoken-form invoice argument(s)",
            must_pass=False,
        )
    )

    # Reported, not gating — conversation quality.
    agent_turns = convo.agent_turns()  # Fixed opening is exempt from conversational brevity.
    bundled = [t[:100] for t in agent_turns if t.count("?") > 1]
    checks.append(Check("one question per turn", not bundled, " | ".join(bundled), must_pass=False))
    long_turns = [t[:60] for t in agent_turns if _word_count_without_facts(t, ms) > 25]
    checks.append(
        Check("turns under 25 words", not long_turns, f"{len(long_turns)} long turn(s)", must_pass=False)
    )
    digit_turns = [t[:80] for t in agent_turns if re.search(r"\d", t)]
    checks.append(
        Check("no digits at all in speech", not digit_turns, " | ".join(digit_turns[:2]), must_pass=False)
    )
    return checks


# --- Post-call comparison -----------------------------------------------------------------------


def to_transcript(convo: SimConversation) -> Transcript:
    return Transcript(
        call_id=f"sim-{convo.scenario_id}",
        turns=[TranscriptTurn(role=role, content=text) for role, text in convo.turns],
        tool_calls=[
            ToolCallRecord(name=c.name, arguments=c.arguments, result=c.result) for c in convo.tool_calls
        ],
        ended_reason=convo.ended_reason,
    )


def compare_postcall(ms: MaterializedScenario, analysis: Any) -> dict[str, dict[str, Any]]:
    """Scenario expectation vs. the pipeline's conclusion, for each field the scenario sets."""
    from collections_agent.eval.compare import _compliance_violation

    exp = ms.scenario.expect.postcall
    pipeline = {
        "outcome": analysis.outcome.outcome.value,
        "promise_captured": analysis.promise.has_promise,
        "promise_complete": analysis.promise.has_promise
        and not analysis.promise.downgraded_to_soft_commitment,
        "dispute_existed": analysis.dispute.has_dispute,
        "compliance_violated": _compliance_violation(analysis.compliance),
        "write_decision": analysis.write_decision.value,
    }
    results: dict[str, dict[str, Any]] = {}
    for key, value in pipeline.items():
        expected = getattr(exp, key)
        if expected is None:
            continue
        agree = value in expected if key == "outcome" else value == expected
        results[key] = {"expected": expected, "pipeline": value, "agree": agree}
    return results
