"""Tests for the transcript parser — see postcall/transcript.py's docstring.

SYNTHETIC_RAW_PAYLOAD mirrors the real shape observed in fixtures/raw/*.json from actual test
calls: a tool invocation is a `tool_calls` message (name + JSON-string arguments) plus a
separate later `tool_call_result` message (result, matched by toolCallId) — not one combined
message. Assert the isolation property too (raw always saved, parsing failure doesn't lose
data).
"""

from __future__ import annotations

import json

from collections_agent.postcall.transcript import (
    extract_recording_link,
    parse_transcript,
    save_parsed,
    save_raw,
)

SYNTHETIC_RAW_PAYLOAD = {
    "id": "call-abc123",
    # No real Vapi payload has a top-level "durationSeconds" field — duration is derived from
    # startedAt/endedAt instead (see transcript.py). 87 seconds apart, to match what this
    # fixture asserted before that was discovered.
    "startedAt": "2026-01-01T00:00:00.000Z",
    "endedAt": "2026-01-01T00:01:27.000Z",
    "cost": 0.12,
    "endedReason": "customer-ended-call",
    "recordingUrl": "https://example.com/rec.wav",
    "messages": [
        {"role": "assistant", "message": "Hello, this is an automated call..."},
        {"role": "user", "message": "Yes, this is Priya."},
        {
            "role": "tool_calls",
            "message": "",
            "toolCalls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "record_ptp", "arguments": '{"amount": 5000}'},
                }
            ],
        },
        {
            "role": "tool_call_result",
            "name": "record_ptp",
            "result": {"ptp_id": "PTP-1"},
            "toolCallId": "call_abc",
        },
    ],
}


def test_save_raw_writes_verbatim_json(tmp_path):
    path = save_raw("call-abc123", SYNTHETIC_RAW_PAYLOAD, raw_dir=tmp_path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == SYNTHETIC_RAW_PAYLOAD


def test_parse_transcript_extracts_turns():
    transcript = parse_transcript(SYNTHETIC_RAW_PAYLOAD)
    assert transcript.call_id == "call-abc123"
    assert len(transcript.turns) == 2
    assert transcript.turns[0].role == "assistant"


def test_parse_transcript_extracts_tool_calls():
    """One record per invocation, not per message — the tool_calls + tool_call_result pair
    above must not double-count."""
    transcript = parse_transcript(SYNTHETIC_RAW_PAYLOAD)
    assert len(transcript.tool_calls) == 1
    call = transcript.tool_calls[0]
    assert call.name == "record_ptp"
    assert call.arguments == {"amount": 5000}
    assert call.result == {"ptp_id": "PTP-1"}


def test_parse_transcript_accepts_string_tool_result():
    payload = {**SYNTHETIC_RAW_PAYLOAD, "messages": [dict(m) for m in SYNTHETIC_RAW_PAYLOAD["messages"]]}
    payload["messages"][-1]["result"] = "No result returned."
    transcript = parse_transcript(payload)
    assert transcript.tool_calls[0].result == {"error": "No result returned."}

    payload["messages"][-1]["result"] = '{"ptp_id":"PTP-2"}'
    assert parse_transcript(payload).tool_calls[0].result == {"ptp_id": "PTP-2"}


def test_parse_transcript_matches_result_by_tool_call_id_with_multiple_calls():
    payload = {
        "id": "call-two-tools",
        "messages": [
            {
                "role": "tool_calls",
                "toolCalls": [
                    {
                        "id": "id-1",
                        "function": {"name": "lookup_invoices", "arguments": '{"account_id": "A"}'},
                    }
                ],
            },
            {
                "role": "tool_calls",
                "toolCalls": [
                    {"id": "id-2", "function": {"name": "record_ptp", "arguments": '{"amount": 100}'}}
                ],
            },
            {
                "role": "tool_call_result",
                "name": "record_ptp",
                "result": {"ptp_id": "PTP-2"},
                "toolCallId": "id-2",
            },
            {
                "role": "tool_call_result",
                "name": "lookup_invoices",
                "result": {"ok": True},
                "toolCallId": "id-1",
            },
        ],
    }
    transcript = parse_transcript(payload)
    assert len(transcript.tool_calls) == 2
    by_name = {c.name: c for c in transcript.tool_calls}
    assert by_name["lookup_invoices"].result == {"ok": True}
    assert by_name["record_ptp"].result == {"ptp_id": "PTP-2"}


def test_parse_transcript_tool_call_without_result_yet():
    payload = {
        "id": "call-pending",
        "messages": [
            {
                "role": "tool_calls",
                "toolCalls": [{"id": "id-1", "function": {"name": "record_ptp", "arguments": "{}"}}],
            },
        ],
    }
    transcript = parse_transcript(payload)
    assert len(transcript.tool_calls) == 1
    assert transcript.tool_calls[0].result is None


def test_parse_transcript_extracts_call_metadata():
    transcript = parse_transcript(SYNTHETIC_RAW_PAYLOAD)
    assert transcript.duration_seconds == 87
    assert transcript.cost_usd == 0.12
    assert transcript.ended_reason == "customer-ended-call"
    assert transcript.recording_url == "https://example.com/rec.wav"


def test_parse_transcript_extracts_started_at():
    from datetime import UTC, datetime

    transcript = parse_transcript(SYNTHETIC_RAW_PAYLOAD)
    assert transcript.started_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_parse_transcript_duration_is_none_without_started_or_ended_at():
    transcript = parse_transcript({"id": "call-no-times"})
    assert transcript.started_at is None
    assert transcript.duration_seconds is None


def test_parse_transcript_handles_missing_fields_gracefully():
    transcript = parse_transcript({"id": "call-empty"})
    assert transcript.call_id == "call-empty"
    assert transcript.turns == []
    assert transcript.tool_calls == []


def test_save_parsed_round_trips(tmp_path):
    transcript = parse_transcript(SYNTHETIC_RAW_PAYLOAD)
    path = save_parsed(transcript, parsed_dir=tmp_path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["call_id"] == "call-abc123"


class TestExtractRecordingLink:
    """See docs/FAILURES.md: the bare recordingUrl field is never independently fetchable
    against a private bucket — only the presigned variant works, and only briefly. These fixture
    shapes mirror real payloads pulled from fixtures/raw/*.json."""

    def test_prefers_presigned_mono_url_when_present(self):
        raw = {
            "recordingUrl": "https://acct.r2.cloudflarestorage.com/hipaa-recordings/x-mono.wav",
            "artifact": {
                "recordingUrl": "https://acct.r2.cloudflarestorage.com/hipaa-recordings/x-mono.wav",
                "presignedMonoUrl": "https://hipaa-recordings.acct.r2.cloudflarestorage.com/x-mono.wav?X-Amz-Signature=abc",
                "presignedUrlsExpiresAt": "2026-09-17T08:32:57.009Z",
            },
        }
        url, expires_at = extract_recording_link(raw)
        assert url == raw["artifact"]["presignedMonoUrl"]
        assert expires_at == "2026-09-17T08:32:57.009Z"

    def test_falls_back_to_bare_url_when_no_presigned_variant_exists(self):
        raw = {"recordingUrl": "https://example.com/rec.wav"}
        url, expires_at = extract_recording_link(raw)
        assert url == "https://example.com/rec.wav"
        assert expires_at is None

    def test_returns_none_when_no_recording_fields_exist(self):
        url, expires_at = extract_recording_link({"id": "call-no-recording"})
        assert url is None
        assert expires_at is None


def test_raw_is_saved_even_if_shape_is_totally_unexpected(tmp_path):
    """The whole point of save_raw being separate from parse_transcript: a payload shape
    parse_transcript can't handle must still be preserved on disk.
    """
    weird_payload = {"totally": {"different": "shape"}, "no_id_field": True}
    path = save_raw("call-weird", weird_payload, raw_dir=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == weird_payload
    # parse_transcript on this either raises or returns a degenerate Transcript — either way
    # the raw file above already exists and is untouched.
