from __future__ import annotations

import json

from collections_agent.eval.latency import call_latency, distribution, latency_report


def raw_call(identifier, turns, average=9999):
    return {
        "id": identifier,
        "artifact": {"performanceMetrics": {"turnLatencies": turns, "turnLatencyAverage": average}},
    }


def test_distribution_uses_turn_median_and_nearest_rank_p95():
    assert distribution([1000, 2000, 3000, 4000]) == {
        "samples": 4,
        "p50_ms": 2500,
        "p95_ms": 4000,
        "mean_ms": 2500,
        "max_ms": 4000,
    }


def test_missing_turns_are_unavailable_not_zero_latency():
    result = call_latency(raw_call("empty", [], average=0))
    assert result["status"] == "unavailable"
    assert result["metrics"]["turn"]["p50_ms"] is None
    assert result["provider_reported_turn_average_ms"] is None


def test_invalid_timings_are_excluded_per_metric():
    result = call_latency(
        raw_call(
            "x",
            [
                {"turnLatency": -1, "modelLatency": 20},
                {"turnLatency": True},
                {"turnLatency": float("nan")},
                {"turnLatency": float("inf")},
                {"turnLatency": 0},
                {"turnLatency": 1000, "transcriberLatency": 0},
            ],
        )
    )
    assert result["metrics"]["turn"]["samples"] == 1
    assert result["invalid_or_missing_turn_measurements"] == 5
    assert result["metrics"]["transcriber"]["p50_ms"] == 0


def test_report_pools_turns_instead_of_averaging_call_averages(tmp_path):
    calls = [
        raw_call("a", [{"turnLatency": 1000}, {"turnLatency": 2000}]),
        raw_call("b", [{"turnLatency": 9000}]),
        raw_call("empty", [], average=0),
    ]
    for index, call in enumerate(calls):
        (tmp_path / f"{index}.json").write_text(json.dumps(call))
    report = latency_report(tmp_path)
    assert report["metrics"]["turn"]["mean_ms"] == 4000
    assert report["metrics"]["turn"]["p50_ms"] == 2000
    assert report["calls_inspected"] == 3
    assert report["calls_without_turn_measurements"] == 1
    assert len(report["source_files"][0]["sha256"]) == 64
    public = latency_report(tmp_path, include_calls=False)
    assert "calls" not in public and "source_files" not in public


def test_empty_report_does_not_invent_results(tmp_path):
    report = latency_report(tmp_path)
    assert report["calls_inspected"] == 0
    assert report["metrics"]["turn"]["samples"] == 0
    assert report["metrics"]["turn"]["p95_ms"] is None


def test_public_report_can_only_include_whitelisted_examples(tmp_path):
    for identifier in ("public-example", "private-visitor"):
        (tmp_path / f"{identifier}.json").write_text(
            json.dumps(raw_call(identifier, [{"turnLatency": 1000}]))
        )
    report = latency_report(tmp_path, include_calls=False, only_call_ids={"public-example"})
    assert report["calls_inspected"] == 1
    assert "private-visitor" not in json.dumps(report)


def test_public_endpoint_returns_aggregate_without_call_identifiers():
    from fastapi.testclient import TestClient

    from collections_agent.webhooks.server import app

    response = TestClient(app).get("/demo/evidence/latency")
    assert response.status_code == 200
    payload = response.json()
    assert payload["calls_inspected"] == 2
    assert payload["metrics"]["turn"]["samples"] == 14
    assert "calls" not in payload and "source_files" not in payload


def test_tool_acknowledgement_never_becomes_completion_or_validation():
    from collections_agent.postcall.transcript import ToolCallRecord
    from collections_agent.webhooks.demo_replay import _tool_call_payload

    accepted = _tool_call_payload(
        ToolCallRecord(name="schedule_callback", arguments={"reason": "test"}, result={"callback_id": "C"})
    )
    assert accepted["kind"] == "Callback requested"
    assert accepted["action_state"] == "accepted"
    assert "validated" not in accepted["status_label"].lower()
    failed = _tool_call_payload(
        ToolCallRecord(name="schedule_callback", arguments={}, result={"error": "failed"})
    )
    assert failed["action_state"] == "failed"
    missing = _tool_call_payload(ToolCallRecord(name="schedule_callback", arguments={}, result=None))
    assert missing["action_state"] == "unknown"


def test_replay_stops_with_explicit_error_when_specialist_fails(monkeypatch):
    import asyncio

    from collections_agent.webhooks import demo_replay

    def fail(**kwargs):
        raise RuntimeError("scripted specialist failure")

    monkeypatch.setattr(demo_replay, "run_postcall", fail)
    monkeypatch.setattr(demo_replay, "_build_timeline", lambda *_: [])

    async def collect():
        return [event async for event in demo_replay.replay_stream("clean")]

    events = asyncio.run(collect())
    assert events[-1].startswith(b"event: session_error")
    assert not any(event.startswith(b"event: final") for event in events)
    assert b"No write decision was produced" in events[-1]
