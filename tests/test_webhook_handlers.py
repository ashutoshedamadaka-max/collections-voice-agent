from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from collections_agent.webhooks import handlers as handlers_module
from collections_agent.webhooks import server as server_module
from collections_agent.webhooks.auth import VAPI_SECRET_HEADER
from collections_agent.webhooks.server import app

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _configure_secret(monkeypatch):
    monkeypatch.setenv("VAPI_SERVER_SECRET", SECRET)


@pytest.fixture
def _redirect_log(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_calls.jsonl"
    monkeypatch.setattr(handlers_module, "TOOL_CALL_LOG_PATH", log_path)
    monkeypatch.setattr(server_module, "WEBHOOK_REQUEST_LOG_PATH", tmp_path / "webhook_requests.jsonl")
    return log_path


@pytest.fixture
def client():
    return TestClient(app)


def test_rejects_missing_secret(client):
    resp = client.post("/vapi/tool-calls", json={"message": {"toolCallList": []}})
    assert resp.status_code == 401


def test_rejects_wrong_secret(client):
    resp = client.post(
        "/vapi/tool-calls",
        json={"message": {"toolCallList": []}},
        headers={VAPI_SECRET_HEADER: "wrong"},
    )
    assert resp.status_code == 401


def test_accepts_correct_secret_and_dispatches_record_ptp(client, _redirect_log):
    body = {
        "message": {
            "type": "tool-calls",
            "toolCallList": [
                {
                    "id": "call-1",
                    "name": "record_ptp",
                    "arguments": {
                        "invoice_ids": ["INV-00001"],
                        "amount": 50000,
                        "date": "2026-10-01",
                        "method": "NEFT",
                    },
                }
            ],
        }
    }
    resp = client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["toolCallId"] == "call-1"
    assert data["results"][0]["result"]["ptp_id"].startswith("PTP-")


def test_record_ptp_with_past_date_returns_structured_error_not_ptp_id(client, _redirect_log):
    body = {
        "message": {
            "toolCallList": [
                {
                    "id": "call-past",
                    "name": "record_ptp",
                    "arguments": {
                        "invoice_ids": ["INV-00001"],
                        "amount": 50000,
                        "date": "2020-01-01",
                        "method": "NEFT",
                    },
                }
            ],
        }
    }
    resp = client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result["error"] == "date_in_the_past"
    assert "ptp_id" not in result


def test_dispatches_string_encoded_arguments(client, _redirect_log):
    encoded_args = json.dumps({"contact_id": "C1", "scope": "this_contact"})
    body = {
        "message": {"toolCallList": [{"id": "call-2", "name": "mark_opt_out", "arguments": encoded_args}]}
    }
    resp = client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    assert resp.json()["results"][0]["result"]["status"] == "confirmed"


def test_unknown_tool_returns_error_result_not_500(client, _redirect_log):
    body = {"message": {"toolCallList": [{"id": "call-3", "name": "not_a_real_tool", "arguments": {}}]}}
    resp = client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    assert resp.status_code == 200
    assert "error" in resp.json()["results"][0]["result"]


def test_tool_call_is_logged_to_jsonl(client, _redirect_log):
    body = {
        "message": {
            "toolCallList": [
                {"id": "call-4", "name": "schedule_callback", "arguments": {"reason": "wrong person"}}
            ]
        }
    }
    client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    lines = _redirect_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["tool"] == "schedule_callback"


def test_raw_request_body_is_logged_before_dispatch(client, _redirect_log, tmp_path):
    """Logged even when toolCallList is empty — the exact case that left no trace anywhere
    during the dispute-call incident (see docs/FAILURES.md)."""
    body = {"message": {"type": "tool-calls", "toolCallList": []}}
    resp = client.post("/vapi/tool-calls", json=body, headers={VAPI_SECRET_HEADER: SECRET})
    assert resp.status_code == 200

    raw_log = tmp_path / "webhook_requests.jsonl"
    lines = raw_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["endpoint"] == "/vapi/tool-calls"
    assert entry["body"] == body


def test_events_endpoint_accepts_and_acks(client):
    resp = client.post(
        "/vapi/events",
        json={"message": {"type": "status-update"}},
        headers={VAPI_SECRET_HEADER: SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_check_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
