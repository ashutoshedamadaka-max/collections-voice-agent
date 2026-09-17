"""Builds the Vapi assistant payload and per-call overrides.

The assistant itself is configured once (model, voice, tools, server URL, hard duration cap,
fixed opening disclosure). The per-call system prompt — built from that call's ContextPack —
is passed as an assistantOverrides system-message override when the call starts
(vapi_client.start_call), not baked into the assistant. Verify exact field names
(assistantOverrides shape in particular) against the Vapi dashboard/docs at build time —
Vapi's API has moved fields around across versions.

Budget note: there is no Anthropic credit for this project, only ~$8 OpenAI and ~$7 Vapi.
The in-call backend model is therefore OpenAI (gpt-4o-mini, cheap and fast enough for
real-time branching), and it must be wired to bill YOUR OpenAI balance rather than
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
from collections_agent.voice.prompt_template import render_system_prompt
from collections_agent.voice.tool_schemas import ALL_TOOLS

MAX_CALL_DURATION_SECONDS = 180  # hard stop (3 minutes), per the design doc's guardrails

# How long Vapi waits for a webhook response (tool-calls, end-of-call-report, status-update)
# before giving up — Server.timeoutSeconds, default 20s. Tightened to bound worst-case dead
# air if the server is unreachable; tools normally return in well under a second (see
# tool_schemas.py's TOOL_TIMEOUT_MESSAGES for what the assistant says while/if it waits).
TOOL_CALL_TIMEOUT_SECONDS = 10

BACKEND_MODEL_PROVIDER = "openai"
BACKEND_MODEL = "gpt-4o-mini"  # cheap, fast enough for real-time branching; billed via OpenAI

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


def opening_disclosure(company_name: str) -> str:
    return (
        f"Hello, this is an automated call from {company_name}'s accounts team "
        "regarding an overdue invoice. This call may be recorded."
    )


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
        "firstMessage": opening_disclosure(company_name),
        "firstMessageMode": "assistant-speaks-first",
        "maxDurationSeconds": MAX_CALL_DURATION_SECONDS,
        "model": _build_model_block(ALL_TOOLS, openai_credential_id),
        "voice": VOICE_CONFIG,
        "transcriber": TRANSCRIBER_CONFIG,
        # `serverUrl` (bare string) doesn't exist on the current Assistant/CreateAssistantDTO
        # schema — verified against Vapi's live OpenAPI spec, not docs prose, since serverUrl
        # was silently accepted-but-inert on an existing assistant (see docs/FAILURES.md).
        # `server.url` is the real field; `server.timeoutSeconds` bounds the wait.
        "server": {
            "url": f"{server_url.rstrip('/')}/vapi/tool-calls",
            "timeoutSeconds": TOOL_CALL_TIMEOUT_SECONDS,
        },
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
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
    return {
        "model": _build_model_block(
            ALL_TOOLS,
            openai_credential_id,
            system_messages=[{"role": "system", "content": system_prompt}],
        ),
    }
