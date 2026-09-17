"""Specialist 3 — dispute classifier (design doc Stage 3)."""

from __future__ import annotations

from collections_agent.models.domain import DisputeClassification
from collections_agent.postcall.openai_client import extract_structured
from collections_agent.postcall.specialists._common import format_transcript_for_llm
from collections_agent.postcall.transcript import Transcript

SYSTEM_PROMPT = (
    "You are classifying a dispute (if any) from a B2B collections call transcript. If the "
    "caller disputed an invoice or charge — never argued with, just report what they said — "
    "identify which invoice, the closest matching reason code, what evidence (if any) was "
    "requested, and which team should handle it: billing (pricing/invoicing errors), sales "
    "(PO/order issues), ops (approvals/process), or logistics (delivery/quantity discrepancies). "
    "If there was no dispute, set has_dispute to false and leave the rest null/empty. "
    "confidence means your certainty in this classification — not whether a dispute was found. "
    "A clear, confident 'no dispute occurred' is high confidence (e.g. 0.9), never low; only "
    "set confidence low when it's genuinely ambiguous whether what was said counts as a dispute."
)


def classify_dispute(transcript: Transcript, api_key: str) -> DisputeClassification:
    return extract_structured(
        SYSTEM_PROMPT, format_transcript_for_llm(transcript), DisputeClassification, api_key
    )
