"""Specialist 1 — outcome extractor (design doc Stage 3)."""

from __future__ import annotations

from collections_agent.models.domain import OutcomeExtraction
from collections_agent.postcall.openai_client import extract_structured
from collections_agent.postcall.specialists._common import format_transcript_for_llm
from collections_agent.postcall.transcript import Transcript

SYSTEM_PROMPT = (
    "You are a QA specialist reviewing a B2B collections call transcript. Classify the "
    "call's outcome using only what was actually said or what the tool calls recorded — "
    "never guess or infer beyond the transcript. Pick the single outcome type that best "
    "matches how the call ended, a reason code only if one clearly applies (otherwise null), "
    "and a short next_action a human or the follow-through job should take. "
    "confidence means your certainty that this classification is correct — not how good or "
    "complete the outcome was. A clear-cut call, even a bad outcome (hostile, no answer), "
    "gets high confidence; only an ambiguous, cut-off, or self-contradictory transcript "
    "should get low confidence."
)


def extract_outcome(
    transcript: Transcript, api_key: str, usage_sink: list | None = None
) -> OutcomeExtraction:
    return extract_structured(
        SYSTEM_PROMPT,
        format_transcript_for_llm(transcript),
        OutcomeExtraction,
        api_key,
        usage_sink=usage_sink,
    )
