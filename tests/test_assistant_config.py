from __future__ import annotations

from datetime import date

from collections_agent.voice.assistant_config import (
    MAX_CALL_DURATION_SECONDS,
    TOOL_CALL_TIMEOUT_SECONDS,
    build_assistant_payload,
    build_call_overrides,
)
from collections_agent.voice.language import opening_message
from collections_agent.voice.tool_schemas import TOOL_NAMES


def test_assistant_payload_has_hard_duration_cap():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["maxDurationSeconds"] == 180 == MAX_CALL_DURATION_SECONDS


def test_assistant_payload_disables_recording():
    """Backs the demo console's "never recorded" claim — verified against
    docs.vapi.ai/assistants/call-recording, not guessed (see docs/FAILURES.md for this
    project's history with unverified Vapi field names)."""
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["artifactPlan"]["recordingEnabled"] is False


def test_call_overrides_also_disable_recording(sample_context_pack):
    """Set in both places: per-call assistantOverrides take precedence over the base
    assistant config, so the guarantee must hold here too, not just at the assistant level."""
    overrides = build_call_overrides(sample_context_pack, "Acme Supplies")
    assert overrides["artifactPlan"]["recordingEnabled"] is False


def test_assistant_payload_first_message_is_generic_disclosure_plus_authority_question():
    """No ContextPack at this level, so no contact name — build_call_overrides supplies the
    personalized version for real calls (see the TestMergedOpening tests below)."""
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["firstMessage"] == opening_message(None, "Acme Supplies")
    assert "automated" in payload["firstMessage"].lower()
    assert "recorded" in payload["firstMessage"].lower()
    assert "accounts payable" in payload["firstMessage"].lower()


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
    assert payload["model"]["model"] == "gpt-4o"
    assert "credentialId" not in payload["model"]


def test_assistant_payload_includes_byok_credential_when_given():
    payload = build_assistant_payload("Acme Supplies", "https://example.com", openai_credential_id="cred-123")
    assert payload["model"]["credentialId"] == "cred-123"


def test_assistant_payload_uses_vapi_voice_and_soniox_transcriber():
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    assert payload["voice"] == {"provider": "vapi", "voiceId": "Naina"}
    assert payload["transcriber"]["provider"] == "soniox"


def test_assistant_payload_sets_stop_speaking_plan_with_word_count_threshold():
    """Regression guard for the 2026-09-17 barge-in stutter loop (docs/FAILURES.md): at Vapi's
    default numWords=0, ~0.2s of any customer voice activity interrupts the assistant — this
    must be a positive threshold so acknowledgementPhrases actually gets a chance to apply."""
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    plan = payload["stopSpeakingPlan"]
    assert plan["numWords"] > 0


def test_assistant_payload_stop_speaking_plan_recognizes_hindi_backchannels():
    """Vapi's default acknowledgementPhrases list is English-only ("okay", "got it", ...) — a
    real Hinglish call was interrupted mid-sentence by "haan" and "theek hai", neither of which
    is in that list."""
    payload = build_assistant_payload("Acme Supplies", "https://example.com")
    phrases = payload["stopSpeakingPlan"]["acknowledgementPhrases"]
    assert "haan" in phrases
    assert "theek hai" in phrases
    assert "okay" in phrases  # English defaults preserved for English calls


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


def test_call_overrides_default_to_english_voice_and_transcriber(sample_context_pack):
    """sample_context_pack's account has preferred_language="en"."""
    overrides = build_call_overrides(sample_context_pack, "Acme Supplies")
    assert overrides["voice"] == {
        "provider": "vapi",
        "voiceId": "Naina",
        "version": "latest",
        "language": "en",
    }
    assert overrides["transcriber"] == {"provider": "soniox", "model": "stt-rt-v5", "language": "en"}


def test_call_overrides_use_hindi_voice_and_transcriber_for_hindi_account(
    base_account, make_invoice, as_of
):
    from collections_agent.precall.context_pack import build_context_pack

    hindi_account = base_account.model_copy(update={"preferred_language": "hi"})
    pack = build_context_pack(
        hindi_account, [make_invoice(hindi_account.account_id, days_overdue=10)], [], [], [], as_of=as_of
    )

    overrides = build_call_overrides(pack, "Acme Supplies")

    assert overrides["voice"]["language"] == "hi"
    assert overrides["transcriber"]["language"] == "hi"


def test_call_overrides_fall_back_to_english_for_a_hinglish_labeled_account(
    base_account, make_invoice, as_of
):
    """Hinglish was dropped 2026-09-29 (docs/FAILURES.md) — an account still marked "hinglish"
    in the Sheet from before that change must get plain English voice/transcriber, not a
    Hinglish-shaped path that no longer exists in the code."""
    from collections_agent.precall.context_pack import build_context_pack

    hinglish_account = base_account.model_copy(update={"preferred_language": "hinglish"})
    pack = build_context_pack(
        hinglish_account,
        [make_invoice(hinglish_account.account_id, days_overdue=10)],
        [],
        [],
        [],
        as_of=as_of,
    )

    overrides = build_call_overrides(pack, "Acme Supplies")

    assert overrides["voice"]["language"] == "en"
    assert overrides["transcriber"]["language"] == "en"
    system_message = overrides["model"]["messages"][0]["content"]
    assert "Respond in English" in system_message


class TestMergedOpening:
    """2026-09-17: the disclosure and the authority question used to be two separate turns —
    a fixed firstMessage, then the model asking the authority question itself after a pause.
    Merging them into one deterministic firstMessage means neither can be reworded, skipped,
    or (for the authority question) asked a second time. See docs/FAILURES.md."""

    def test_call_overrides_set_a_personalized_first_message(self, sample_context_pack):
        overrides = build_call_overrides(sample_context_pack, "Acme Supplies")
        assert overrides["firstMessage"] == (
            "Hello, this is an automated call from Acme Supplies's accounts team about an "
            "overdue invoice. This call may be recorded. Am I speaking with "
            f"{sample_context_pack.contact_name}?"
        )

    def test_first_message_contains_disclosure_and_authority_question_in_one_line(
        self, sample_context_pack
    ):
        overrides = build_call_overrides(sample_context_pack, "Acme Supplies")
        first_message = overrides["firstMessage"]
        assert "recorded" in first_message.lower()
        assert sample_context_pack.contact_name in first_message
        assert first_message.count("?") == 1  # exactly one question, not two separate turns

    def test_hinglish_labeled_account_gets_the_english_opening(self, base_account, make_invoice, as_of):
        """Hinglish was dropped 2026-09-29 — a Sheet row still marked "hinglish" gets the
        English opening, not a Hinglish-shaped one that no longer exists."""
        from collections_agent.precall.context_pack import build_context_pack

        hinglish_account = base_account.model_copy(update={"preferred_language": "hinglish"})
        pack = build_context_pack(
            hinglish_account,
            [make_invoice(hinglish_account.account_id, days_overdue=10)],
            [],
            [],
            [],
            as_of=as_of,
        )

        overrides = build_call_overrides(pack, "Acme Supplies")

        first_message = overrides["firstMessage"]
        assert pack.contact_name in first_message
        assert "record" in first_message.lower()
        assert all(ord(ch) < 0x900 for ch in first_message)  # plain English, no Devanagari
