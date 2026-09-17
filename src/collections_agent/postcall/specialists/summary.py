"""Specialist 5 — call summary (added 2026-09-17, after Step 5).

Vapi's own `call.analysis.summary` field exists in the payload schema and its `SummaryPlan`
defaults to `enabled: true` with a 2-3 sentence prompt — but it came back empty on every real
call pulled so far (`analysisCostBreakdown.summary` cost is 0, meaning it was never actually
invoked), and its configuration path (`assistant.analysisPlan`) is marked deprecated in Vapi's
current OpenAPI spec. Rather than depend on a deprecated mechanism that has never once fired
for this project, this specialist generates the summary itself from the same transcript the
other four specialists already see.

Purely descriptive, unlike promise/compliance — a summary being imprecise doesn't misstate
money or violate a rule, so it's never part of the supervisor's confidence gate (see
pipeline.py) and doesn't need the extraction/validation split those specialists use.
"""

from __future__ import annotations

from collections_agent.models.domain import CallSummary
from collections_agent.postcall.openai_client import extract_structured
from collections_agent.postcall.specialists._common import format_transcript_for_llm
from collections_agent.postcall.transcript import Transcript

SYSTEM_PROMPT = (
    "You are summarizing a B2B collections call for a human who will read this later instead "
    "of the full transcript. Write exactly 2-3 sentences covering: what the customer said or "
    "the situation on the call, what was agreed (a promise to pay, a dispute, a reason for "
    "non-payment, or nothing), and what happens next. Use only what's in the transcript — "
    "never guess or add information that wasn't said. Be factual and neutral, not "
    "conversational, and do not include a preamble like 'This call was about'."
)


def extract_summary(transcript: Transcript, api_key: str) -> CallSummary:
    return extract_structured(
        SYSTEM_PROMPT, format_transcript_for_llm(transcript), CallSummary, api_key
    )
