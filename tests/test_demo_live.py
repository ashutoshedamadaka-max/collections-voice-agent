from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from collections_agent.config import Settings
from collections_agent.postcall.transcript import TranscriptTurn
from collections_agent.webhooks import demo_caps, demo_live, demo_sessions, server
from collections_agent.webhooks.demo_fixtures import DEMO_ACCOUNT_ID


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        vapi_public_key="public",
        vapi_assistant_id="assistant",
        vapi_server_secret="secret",
        openai_api_key="test",
        demo_state_db_path=str(tmp_path / "state.db"),
        demo_live_enabled=True,
    )
    for module in (demo_live, server):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setenv("VAPI_SERVER_SECRET", "secret")
    monkeypatch.setattr(server, "WEBHOOK_REQUEST_LOG_PATH", tmp_path / "requests.jsonl")
    return settings, TestClient(server.app)


def reserve(client):
    response = client.post("/demo/live/config")
    assert response.status_code == 200
    assert response.json()["available"]
    return response.json()["sessionId"]


def message(session_id, call_id=None, kind="end-of-call-report"):
    return {
        "type": kind,
        "call": {
            "id": call_id or str(uuid.uuid4()),
            "assistantOverrides": {"variableValues": {"demo_session_id": session_id}},
        },
        "startedAt": "2026-10-07T10:00:00Z",
        "endedAt": "2026-10-07T10:00:20Z",
        "cost": 0.15,
        "messages": [{"role": "user", "message": "I can pay tomorrow."}],
    }


def test_reservation_is_post_only_no_usage_and_single_caller(setup):
    settings, client = setup
    assert client.get("/demo/live/config").status_code in (404, 405)
    session_id = reserve(client)
    assert demo_caps.calls_today_count(settings.demo_state_db_path) == 0
    assert client.post("/demo/live/config").json()["reason"] == "busy"
    assert client.get("/demo/live/status").json()["reason"] == "busy"
    assert client.post(f"/demo/live/{session_id}/cancel").status_code == 200
    assert client.get("/demo/live/status").json()["available"]


def test_other_visitor_cannot_read_connect_or_cancel(setup):
    _, client = setup
    sid = reserve(client)
    other = TestClient(server.app)
    assert other.get(f"/demo/live/{sid}").status_code == 404
    for action in ["connected", "cancel"]:
        assert other.post(f"/demo/live/{sid}/{action}", json={"callId": str(uuid.uuid4())}).status_code == 404
    assert client.post("/demo/live/config", headers={"Origin": "https://other.example"}).status_code == 403


def test_connection_counts_once_and_cannot_be_refunded(setup):
    settings, client = setup
    sid = reserve(client)
    body = {"callId": str(uuid.uuid4())}
    for _ in range(2):
        assert client.post(f"/demo/live/{sid}/connected", json=body).status_code == 200
    client.post(f"/demo/live/{sid}/cancel")
    assert demo_caps.calls_today_count(settings.demo_state_db_path) == 1
    assert demo_sessions.get(settings.demo_state_db_path, sid)["state"] == "active"
    assert client.get("/demo/live/status").json()["reason"] == "visitor_window"


def test_browser_end_requires_connected_owned_call(setup):
    _, client = setup
    sid = reserve(client)
    call_id = str(uuid.uuid4())
    body = {"callId": call_id, "durationSeconds": 20, "turns": [{"role": "bot", "content": "Hello."}]}
    assert client.post(f"/demo/live/{sid}/ended", json=body).status_code == 409
    assert client.post(f"/demo/live/{sid}/connected", json={"callId": call_id}).status_code == 200
    wrong_call = {**body, "callId": str(uuid.uuid4())}
    assert client.post(f"/demo/live/{sid}/ended", json=wrong_call).status_code == 409
    wrong_turn = {**body, "turns": [{"role": "system", "content": "x"}]}
    assert client.post(f"/demo/live/{sid}/ended", json=wrong_turn).status_code == 422


def test_browser_transcript_recovers_missing_provider_report(setup, monkeypatch):
    settings, client = setup
    sid = reserve(client)
    call_id = str(uuid.uuid4())
    client.post(f"/demo/live/{sid}/connected", json={"callId": call_id})

    async def no_wait(_):
        return None

    async def analyze(settings, transcript, emit):
        assert transcript.call_id == call_id
        assert [turn.content for turn in transcript.turns] == ["Hello.", "I can pay tomorrow."]
        assert transcript.cost_usd is None
        emit("final", {"cost": {"pipeline_cost": 0.01}})

    monkeypatch.setattr(demo_live.asyncio, "sleep", no_wait)
    monkeypatch.setattr(demo_live, "_analyze", AsyncMock(side_effect=analyze))
    asyncio.run(
        demo_live._recover_ended_call(
            settings.demo_state_db_path,
            sid,
            call_id,
            [
                TranscriptTurn(role="bot", content="Hello."),
                TranscriptTurn(role="user", content="I can pay tomorrow."),
            ],
            20,
        )
    )
    assert demo_sessions.get(settings.demo_state_db_path, sid)["state"] == "complete"
    assert demo_caps.calls_today_count(settings.demo_state_db_path) == 1


def test_provider_report_wins_over_browser_recovery(setup, monkeypatch):
    settings, client = setup
    sid = reserve(client)
    call_id = str(uuid.uuid4())
    client.post(f"/demo/live/{sid}/connected", json={"callId": call_id})
    with demo_sessions.connect(settings.demo_state_db_path) as conn:
        conn.execute("UPDATE live_sessions SET state='analyzing',processing=1 WHERE id=?", (sid,))

    async def no_wait(_):
        return None

    handler = AsyncMock()
    monkeypatch.setattr(demo_live.asyncio, "sleep", no_wait)
    monkeypatch.setattr(demo_live, "_finish_analysis", handler)
    asyncio.run(demo_live._recover_ended_call(settings.demo_state_db_path, sid, call_id, [], 20))
    handler.assert_not_awaited()


def test_config_missing_secrets_is_unavailable(setup):
    settings, client = setup
    settings.vapi_public_key = ""
    payload = client.post("/demo/live/config").json()
    assert payload["reason"] == "configuration"
    assert "assistantOverrides" not in payload


def test_rollout_disabled_and_tls_proxy_origin(setup):
    settings, client = setup
    settings.demo_live_enabled = False
    assert client.get("/demo/live/status").json()["reason"] == "configuration"
    settings.demo_live_enabled = True
    response = client.post(
        "/demo/live/config", headers={"Origin": "https://testserver", "X-Forwarded-Proto": "https"}
    )
    assert response.json()["available"]
    assert "Secure" in response.headers["set-cookie"]


def test_provider_correlation_rejects_conflicting_session_marker(setup):
    settings, client = setup
    sid = reserve(client)
    event = message(sid)
    assert demo_sessions.correlate(settings.demo_state_db_path, event) == sid
    event["call"]["assistantOverrides"]["variableValues"]["demo_session_id"] = "other-session"
    assert demo_sessions.correlate(settings.demo_state_db_path, event) is None


def test_timed_out_reservation_releases_without_usage(setup):
    settings, client = setup
    sid = reserve(client)
    with demo_sessions.connect(settings.demo_state_db_path) as conn:
        conn.execute("UPDATE live_sessions SET expires=0 WHERE id=?", (sid,))
    assert client.get("/demo/live/status").json()["available"]
    assert demo_caps.calls_today_count(settings.demo_state_db_path) == 0
    assert demo_sessions.events(settings.demo_state_db_path, sid, 0)[-1][1] == "session_error"


def test_provider_event_never_uses_current_session_as_fallback(setup):
    settings, client = setup
    sid = reserve(client)
    assert demo_sessions.correlate(settings.demo_state_db_path, {"type": "tool-calls"}) is None
    assert demo_sessions.correlate(settings.demo_state_db_path, message("someone-else")) is None
    assert demo_sessions.events(settings.demo_state_db_path, sid, 0) == []


@pytest.mark.parametrize("endpoint", ["/vapi/tool-calls", "/vapi/events"])
def test_end_report_routes_to_analysis_on_both_urls(setup, monkeypatch, endpoint):
    _, client = setup
    handler = AsyncMock()
    monkeypatch.setattr(server, "handle_end_of_call", handler)
    payload = message(reserve(client))
    response = client.post(endpoint, json={"message": payload}, headers={"X-Vapi-Secret": "secret"})
    assert response.status_code == 200
    handler.assert_awaited_once_with(payload)


def test_duplicate_end_report_charges_and_analyzes_once(setup, monkeypatch):
    settings, client = setup
    sid = reserve(client)
    payload = message(sid)

    async def analyze(settings, transcript, emit):
        emit("final", {"cost": {"pipeline_cost": 0.01}})

    handler = AsyncMock(side_effect=analyze)
    monkeypatch.setattr(demo_live, "_analyze", handler)
    asyncio.run(demo_live.handle_end_of_call(payload))
    asyncio.run(demo_live.handle_end_of_call(payload))
    assert handler.await_count == 1
    assert demo_caps.calls_today_count(settings.demo_state_db_path) == 1
    assert demo_caps.cumulative_spend(settings.demo_state_db_path) == pytest.approx(0.16)
    response = client.get(f"/demo/live/{sid}")
    assert "event: final" in response.text
    event_id = demo_sessions.events(settings.demo_state_db_path, sid, 0)[0][0]
    assert (
        "event: final" not in client.get(f"/demo/live/{sid}", headers={"Last-Event-ID": str(event_id)}).text
    )


def test_pipeline_failure_releases_session_and_emits_error(setup, monkeypatch):
    settings, client = setup
    sid = reserve(client)
    monkeypatch.setattr(demo_live, "_analyze", AsyncMock(side_effect=RuntimeError("provider unavailable")))
    asyncio.run(demo_live.handle_end_of_call(message(sid)))
    assert demo_sessions.get(settings.demo_state_db_path, sid)["state"] == "failed"
    assert "event: session_error" in client.get(f"/demo/live/{sid}").text


def test_live_invoice_lookup_uses_fixtures_and_deduplicates(setup, monkeypatch):
    settings, client = setup
    sid = reserve(client)
    monkeypatch.setattr(server, "dispatch", lambda *args: pytest.fail("Must not contact Sheets"))
    payload = message(sid, kind="tool-calls")
    payload["toolCallList"] = [
        {"id": "tool-1", "name": "lookup_invoices", "arguments": {"account_id": DEMO_ACCOUNT_ID}}
    ]
    replies = [
        client.post("/vapi/tool-calls", json={"message": payload}, headers={"X-Vapi-Secret": "secret"}).json()
        for _ in range(2)
    ]
    assert replies[0] == replies[1]
    assert len(json.loads(replies[0]["results"][0]["result"])["invoices"]) == 2
    assert len(demo_sessions.events(settings.demo_state_db_path, sid, 0)) == 1


def test_pipeline_exception_does_not_wait_for_missing_specialists(setup, monkeypatch):
    settings, _ = setup

    def fail(**kwargs):
        raise RuntimeError("LLM failure")

    monkeypatch.setattr(demo_live, "run_postcall", fail)
    transcript = demo_live.parse_transcript({**message("unused"), "id": "call"})

    async def run():
        with pytest.raises(RuntimeError, match="LLM failure"):
            await asyncio.wait_for(demo_live._analyze(settings, transcript, lambda *args: None), 3)

    asyncio.run(run())
