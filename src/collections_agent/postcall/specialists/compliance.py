"""Specialist 4 — compliance and QA reviewer (design doc Stage 3).

Checks the call against the same hard rules the agent's own prompt was given
(voice/templates/system_prompt.j2), so the reviewer and the agent are graded against one
source of truth rather than a separately-invented checklist. The LLM judges qualitative
compliance (disclosure, authority, permitted facts, discounts, threats); whether a stated
"total" figure is arithmetically consistent with the actual invoice data is a deterministic
check done in code (check_arithmetic_consistency), never asked of the model — a model grading
its own arithmetic has the same failure mode that produced a wrong amount_within_outstanding
check in the promise validator (see docs/FAILURES.md). A misstated total is treated as a
compliance violation, not a QA nicety: it caps qa_score in code regardless of the model's
qualitative score.
"""

from __future__ import annotations

import re

from collections_agent.models.domain import ComplianceExtraction, ComplianceReview, Invoice
from collections_agent.postcall.openai_client import extract_structured
from collections_agent.postcall.specialists._common import format_transcript_for_llm
from collections_agent.postcall.transcript import Transcript

SYSTEM_PROMPT = (
    "You are a compliance and QA reviewer for a B2B collections call transcript. Check, using "
    "only what's in the transcript: did the agent disclose it was an automated call and that "
    "it may be recorded (disclosed_automated); did it confirm the person's authority over "
    "accounts payable before stating any invoice amount (verified_authority); did it ever "
    "state a number, date, or invoice reference that wasn't given to it as fact "
    "(stayed_within_permitted_facts — true means it did NOT go outside its facts); did it "
    "affirmatively offer, hint at, or negotiate any discount or waiver "
    "(promised_discount_or_waiver); did it mention legal action, credit holds, agencies, or "
    "other consequences (threatened_consequences). "
    "promised_discount_or_waiver means the agent proposed, suggested, or agreed to reduce or "
    "waive an amount — the topic of a discount coming up is not itself a violation, only the "
    "agent offering one is. A refusal is compliant, however it's phrased and in whatever "
    "language, even if the agent says it will relay the request to someone else: "
    "'that's not something I can adjust, I'll flag it to the team' is compliant, not a "
    "violation — and so is its Hindi equivalent, 'यह संभव नहीं है, मैं इसे टीम को बताऊँगा' "
    "('this isn't possible, I'll tell the team'). Both refuse the discount and route the "
    "request; neither grants anything. Set promised_discount_or_waiver=false for any clear "
    "refusal, no matter how the topic came up. "
    "Score the call 0-1 on qa_score (1 = fully compliant and professional) and explain any "
    "violations briefly in notes. Do not check arithmetic or whether numbers are internally "
    "consistent — a separate deterministic system does that. confidence means your certainty "
    "in this judgment being correct — not how compliant the call was. A confident 'no "
    "violations found' is high confidence, not low; only set it low when the transcript itself "
    "is too short or garbled to judge."
)

# Matches "total ... <number>" within ~40 chars, e.g. "the total outstanding amount is 840,000"
# or "total: 1,110,000.50" — tuned to this project's own prompt phrasing (system_prompt.j2's
# "Total outstanding: {{ total_outstanding }}"), not general-purpose NLU.
_TOTAL_PATTERN = re.compile(r"total[^.\n\d]{0,40}?([\d][\d,]*(?:\.\d+)?)", re.IGNORECASE)

# A garbled transcript (pre-Soniox, digit-by-digit STT output like "3 6 5 5. 0 0 0. 0 0" for
# 365,500) makes the regex above grab a lone leading digit ("3") as if it were the whole
# figure — that's not a plausible B2B invoice total, it's a transcription artifact. Every
# invoice amount in this project is well above this floor, so anything below it is noise, not
# a number worth comparing (see docs/FAILURES.md).
MIN_PLAUSIBLE_CURRENCY_AMOUNT = 1000.0


def _extract_stated_totals(transcript: Transcript) -> list[float]:
    totals = []
    for turn in transcript.turns:
        if turn.role not in ("bot", "assistant"):
            continue
        for match in _TOTAL_PATTERN.finditer(turn.content):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if value >= MIN_PLAUSIBLE_CURRENCY_AMOUNT:
                totals.append(value)
    return totals


def check_arithmetic_consistency(transcript: Transcript, invoices: list[Invoice]) -> tuple[bool, str]:
    """Pure code, no LLM. Returns (misstated_total, detail).

    An unverifiable check (no invoice data, or no plausible currency figure found near
    "total") returns False, not a violation — a queue full of false positives from garbled
    transcripts is a queue nobody reads, which defeats the point of having one. `detail`
    still explains *why* when unverifiable, so that distinction isn't silently lost.
    """
    if not invoices:
        return False, "no invoice data provided — could not verify"
    actual_total = round(sum(inv.outstanding() for inv in invoices), 2)
    stated_totals = _extract_stated_totals(transcript)
    if not stated_totals:
        return False, "no plausible stated total found in transcript — could not verify"
    mismatches = [t for t in stated_totals if abs(t - actual_total) > 1.0]
    if not mismatches:
        return False, ""
    return True, f"stated total(s) {mismatches} do not match the actual outstanding total {actual_total:,.2f}"


def review_compliance(transcript: Transcript, invoices: list[Invoice], api_key: str) -> ComplianceReview:
    extraction = extract_structured(
        SYSTEM_PROMPT, format_transcript_for_llm(transcript), ComplianceExtraction, api_key
    )
    misstated_total, detail = check_arithmetic_consistency(transcript, invoices)

    qa_score = extraction.qa_score
    notes = extraction.notes
    if misstated_total:
        qa_score = min(qa_score, 0.5)
        notes = f"{notes} Arithmetic check (code): {detail}.".strip()

    return ComplianceReview(
        disclosed_automated=extraction.disclosed_automated,
        verified_authority=extraction.verified_authority,
        stayed_within_permitted_facts=extraction.stayed_within_permitted_facts,
        promised_discount_or_waiver=extraction.promised_discount_or_waiver,
        threatened_consequences=extraction.threatened_consequences,
        misstated_total=misstated_total,
        qa_score=qa_score,
        notes=notes,
        confidence=extraction.confidence,
    )
