"""Builds the Vapi assistant payload and per-call overrides.

The assistant itself is configured once (model, voice, tools, server URL, hard duration cap, a
generic fallback opening). The per-call system prompt and firstMessage — both built from that
call's ContextPack — are passed as assistantOverrides when the call starts
(vapi_client.start_call), not baked into the assistant. firstMessage carries the disclosure and
the authority question merged into one deterministic line (2026-09-17, see docs/FAILURES.md):
both are spoken verbatim by Vapi before the model runs, so the model can't reword, skip, or
re-ask either — the two things in this whole design that most need to never vary. Verify exact
field names (assistantOverrides shape in particular) against the Vapi dashboard/docs at build
time — Vapi's API has moved fields around across versions.

Budget note: there is no Anthropic credit for this project, only ~$8 OpenAI and ~$7 Vapi.
The in-call backend model is therefore OpenAI — gpt-4o-mini originally for cost, currently
gpt-4o as a live experiment (see BACKEND_MODEL below) — and it must be wired to bill YOUR
OpenAI balance rather than
Vapi-hosted credits — pass `openai_credential_id` (from Vapi Dashboard -> Provider Keys,
after adding your own OpenAI key there) into both builders below. Voice and transcriber are
also picked for cost: Vapi's own built-in voice (no extra provider key/cost beyond Vapi
credits) and Deepgram nova-2 (Vapi's inexpensive default transcriber). Re-verify current
pricing/voice ids against your Vapi dashboard before recording calls.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from collections_agent.models.domain import ContextPack
from collections_agent.voice.language import opening_message, transcriber_language, voice_language
from collections_agent.voice.prompt_template import render_system_prompt
from collections_agent.voice.tool_schemas import ALL_TOOLS

MAX_CALL_DURATION_SECONDS = 180  # hard stop (3 minutes), per the design doc's guardrails

# Verified against docs.vapi.ai/assistants/call-recording: `artifactPlan.recordingEnabled`
# (boolean, defaults true) works both in the base assistant config and in per-call
# assistantOverrides — set in both places here so the demo console's "never recorded" claim
# is actually true, not aspirational. `transcriptPlan`/`loggingEnabled` are deliberately left
# alone: the post-call pipeline needs the transcript.
ARTIFACT_PLAN = {"recordingEnabled": False}

# How long Vapi waits for a webhook response (tool-calls, end-of-call-report, status-update)
# before giving up — Server.timeoutSeconds, default 20s. Tightened to bound worst-case dead
# air if the server is unreachable; tools normally return in well under a second (see
# tool_schemas.py's TOOL_TIMEOUT_MESSAGES for what the assistant says while/if it waits).
TOOL_CALL_TIMEOUT_SECONDS = 10

BACKEND_MODEL_PROVIDER = "openai"
# gpt-4o-mini ignored four separate, correct, already-fixed instructions in one call
# (2026-09-26, see docs/FAILURES.md) — narrating record_ptp/schedule_callback instead of
# calling them, reading invoice numbers/amounts as raw text instead of the (say "...") forms,
# skipping the full-or-partial amount question. An English-language control call reproduced
# most of these too (not language-linked), so this is a live experiment: gpt-4o, temporarily,
# to find out whether a more capable model actually follows the same prompt before any more
# prompt rewrites. Cost difference is trivial at this call volume. Revert to gpt-4o-mini if
# this doesn't hold up, or keep gpt-4o if it does — not yet decided either way.
BACKEND_MODEL = "gpt-4o"

# Vapi's own built-in TTS voice — no separate ElevenLabs/PlayHT account or key needed, and
# billed through the Vapi credits already budgeted. "Naina" (Indian American accent) fits the
# Indian business context better than the original default; confirm the voiceId is still
# valid in your dashboard before recording calls (Vapi's voice catalog changes over time).
VOICE_CONFIG = {"provider": "vapi", "voiceId": "Naina"}

# Soniox stt-rt-v5 — higher-accuracy real-time transcription than the original default
# (Deepgram nova-2). Model costs are now cheap enough that transcription accuracy is worth
# paying for: nova-2 misheard invoice numbers ("KA-3281" -> "Quinus 3281") and dropped a full
# customer utterance in testing (see docs/FAILURES.md).
TRANSCRIBER_CONFIG = {"provider": "soniox", "model": "stt-rt-v5", "language": "en"}

# Fixes a real barge-in stutter loop found on a Hinglish test call (2026-09-17, see
# docs/FAILURES.md): with numWords at Vapi's default (0), any ~0.2s of detected customer voice
# activity interrupts the assistant regardless of what was said, and Vapi's built-in
# acknowledgementPhrases safety net (which stops short backchannel words from interrupting) is
# English-only — "haan", "theek hai", "mil gaya" aren't in it. The assistant was cut off
# mid-sentence twice in the same call by exactly this. numWords>0 makes the word-count
# threshold (and the phrase lists below) actually apply instead of raw voice-activity timing.
STOP_SPEAKING_PLAN = {
    "numWords": 3,
    "voiceSeconds": 0.3,
    "backoffSeconds": 1,
    "acknowledgementPhrases": [
        # Vapi's English defaults — kept so English calls are unaffected.
        "i understand", "i see", "i got it", "i hear you", "im listening", "im with you",
        "right", "okay", "ok", "sure", "alright", "got it", "understood", "yeah", "yes",
        "uh-huh", "mm-hmm", "gotcha", "mhmm", "ah", "yeah okay", "yeah sure",
        # Hindi/Hinglish backchannels — the observed cause of the stutter loop.
        "haan", "haan ji", "ji", "ji haan", "haanji", "theek hai", "thik hai", "achha",
        "acha", "samajh gaya", "samajh gayi", "mil gaya", "mil gayi",
    ],
}


def _build_model_block(
    tools: list[dict[str, Any]],
    openai_credential_id: str | None = None,
    system_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "provider": BACKEND_MODEL_PROVIDER,
        "model": BACKEND_MODEL,
        "tools": tools,
    }
    if system_messages:
        block["messages"] = system_messages
    if openai_credential_id:
        # BYOK: bills your OpenAI balance instead of Vapi-hosted credits. Field name is a
        # best guess from Vapi's docs — confirm against your dashboard/API version; if wrong,
        # the assistant will silently fall back to Vapi-hosted OpenAI credits instead of
        # failing loudly, so check your Vapi usage dashboard after the first test call.
        block["credentialId"] = openai_credential_id
    return block


def build_assistant_payload(
    company_name: str,
    server_url: str,
    openai_credential_id: str | None = None,
) -> dict[str, Any]:
    """Payload for `POST /assistant` — the assistant's fixed, call-independent config."""
    return {
        "name": "collections-agent-v1",
        # Generic fallback only (no contact name — this config has no ContextPack). Real calls
        # override this in build_call_overrides with the personalized version. Disclosure and
        # the authority question are merged into one deterministic firstMessage (2026-09-17,
        # see docs/FAILURES.md) — both are spoken verbatim by Vapi before the model runs at
        # all, so neither can be reworded, skipped, or asked twice.
        "firstMessage": opening_message(None, company_name),
        "firstMessageMode": "assistant-speaks-first",
        "maxDurationSeconds": MAX_CALL_DURATION_SECONDS,
        "model": _build_model_block(ALL_TOOLS, openai_credential_id),
        "voice": VOICE_CONFIG,
        "transcriber": TRANSCRIBER_CONFIG,
        "stopSpeakingPlan": STOP_SPEAKING_PLAN,
        # `serverUrl` (bare string) doesn't exist on the current Assistant/CreateAssistantDTO
        # schema — verified against Vapi's live OpenAPI spec, not docs prose, since serverUrl
        # was silently accepted-but-inert on an existing assistant (see docs/FAILURES.md).
        # `server.url` is the real field; `server.timeoutSeconds` bounds the wait.
        "server": {
            "url": f"{server_url.rstrip('/')}/vapi/tool-calls",
            "timeoutSeconds": TOOL_CALL_TIMEOUT_SECONDS,
        },
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
        "artifactPlan": ARTIFACT_PLAN,
    }


def build_call_overrides(
    context_pack: ContextPack,
    company_name: str,
    openai_credential_id: str | None = None,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Per-call `assistantOverrides` for `POST /call` — injects the rendered system prompt.

    `current_date` should be the same `as_of` the caller used to build `context_pack`, so the
    agent's notion of "today" (for resolving a bare day number like "the 28th") matches the
    date its facts were computed against — pass it explicitly rather than relying on the
    server-local default.
    """
    system_prompt = render_system_prompt(context_pack, company_name, current_date)
    # Language is a pre-call property of the account (context_pack.preferred_language), not
    # something negotiated during the call — see voice/language.py and docs/FAILURES.md.
    # `version: "latest"` opts into Vapi Voices' current TTS generation (verified against the
    # live OpenAPI spec: the `vapi` voice provider's `version` field accepts the literal string
    # "latest", not just an integer).
    return {
        "firstMessage": opening_message(
            context_pack.preferred_language, company_name, context_pack.contact_name
        ),
        "firstMessageMode": "assistant-speaks-first",
        "model": _build_model_block(
            ALL_TOOLS,
            openai_credential_id,
            system_messages=[{"role": "system", "content": system_prompt}],
        ),
        "voice": {
            **VOICE_CONFIG,
            "version": "latest",
            "language": voice_language(context_pack.preferred_language),
        },
        "transcriber": {
            **TRANSCRIBER_CONFIG,
            "language": transcriber_language(context_pack.preferred_language),
        },
        "artifactPlan": ARTIFACT_PLAN,
    }
