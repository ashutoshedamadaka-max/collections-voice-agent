from __future__ import annotations

from datetime import date

from collections_agent.voice.assistant_config import (
    MAX_CALL_DURATION_SECONDS,
    TOOL_CALL_TIMEOUT_SECONDS,
    build_assistant_payload,
    build_call_overrides,
    opening_disclosure,
)
from collections_agent.voice.tool_schemas import TOOL_NAMES


def test_assistant_payload_has_hard_duration_cap():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["maxDurationSeconds"] == 180 == MAX_CALL_DURATION_SECONDS


def test_assistant_payload_first_message_is_disclosure():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["firstMessage"] == opening_disclosure("Acme Supplies")
    assert "automated" in payload["firstMessage"].lower()
    assert "recorded" in payload["firstMessage"].lower()


def test_assistant_payload_server_url_points_at_tool_calls_endpoint():
    payload = build_assistant_payload("Acme Supplies", "https://tunnel.example.com/")
    assert payload["server"]["url"] == "https://tunnel.example.com/vapi/tool-calls"
    assert payload["server"]["timeoutSeconds"] == TOOL_CALL_TIMEOUT_SECONDS


def test_assistant_payload_includes_all_tools():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    tool_names_in_payload = {t["function"]["name"] for t in payload["model"]["tools"]}
    assert tool_names_in_payload == set(TOOL_NAMES)


def test_assistant_payload_uses_openai_not_anthropic():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["model"]["provider"] == "openai"
    assert payload["model"]["model"] == "gpt-4o-mini"
    assert "credentialId" not in payload["model"]


def test_assistant_payload_includes_byok_credential_when_given():
    payload = build_assistant_payload("Acme Supplies", "https://example.com", openai_credential_id="cred-123")
    assert payload["model"]["credentialId"] == "cred-123"


def test_assistant_payload_uses_vapi_voice_and_soniox_transcriber():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["voice"] == {"provider": "vapi", "voiceId": "Naina"}
    assert payload["transcriber"]["provider"] == "soniox"


def test_call_overrides_embeds_rendered_prompt(sample_context_pack):
    overrides = build_call_overrides(sample_context_pack, "Acme Supplies")
    system_message = overrides["model"]["messages"][0]["content"]
    assert system_message.startswith("# ROLE")
    assert sample_context_pack.contact_name in system_message


def test_call_overrides_use_openai_and_byok_credential(sample_context_pack):
    overrides = build_call_overrides(sample_context_pack, "Acme Supplies", openai_credential_id="cred-123")
    assert overrides["model"]["provider"] == "openai"
    assert overrides["model"]["credentialId"] == "cred-123"


def test_call_overrides_embed_the_given_current_date(sample_context_pack):
    overrides = build_call_overrides(sample_context_pack, "Acme Supplies", current_date=date(2026, 9, 16))
    system_message = overrides["model"]["messages"][0]["content"]
    assert "Today's date is 2026-09-16" in system_message
