from __future__ import annotations

import uuid

import httpx
import respx
from fastapi.testclient import TestClient

from collections_agent.webhooks.local_preview import HOSTED, app


def client():
    return TestClient(app, base_url="http://127.0.0.1:8766")


def test_local_ui_is_served_without_remote_requests():
    response = client().get("/demo")
    assert response.status_code == 200
    assert "Talk to the agent" in response.text


@respx.mock
def test_status_uses_hosted_caps_and_does_not_forward_local_secrets():
    route = respx.get(HOSTED + "/demo/live/status").mock(
        return_value=httpx.Response(200, json={"available": True})
    )
    response = client().get("/demo/live/status", headers={"Authorization": "local-secret"})
    assert response.json()["available"]
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_session_cookie_and_origin_stay_consistent_across_proxy():
    visitor = str(uuid.uuid4())
    config = respx.post(HOSTED + "/demo/live/config").mock(
        return_value=httpx.Response(
            200,
            json={"available": True},
            headers={"set-cookie": f"demo_visitor_id={visitor}; Secure; HttpOnly; SameSite=lax; Path=/"},
        )
    )
    status = respx.get(HOSTED + "/demo/live/status").mock(return_value=httpx.Response(200, json={}))
    browser = client()
    response = browser.post("/demo/live/config", headers={"Origin": "http://127.0.0.1:8766"})
    assert "Secure" not in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    browser.get("/demo/live/status")
    assert status.calls.last.request.headers["cookie"] == f"demo_visitor_id={visitor}"
    assert config.calls.last.request.headers["origin"] == HOSTED


@respx.mock
def test_cross_origin_non_loopback_and_private_paths_are_rejected():
    browser = client()
    assert browser.post("/demo/live/config", headers={"origin": "https://evil.example"}).status_code == 403
    assert browser.post("/demo/live/config", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert browser.get("/demo", headers={"host": "public.example"}).status_code == 403
    assert browser.post("/vapi/tool-calls").status_code == 404
    assert browser.get("/diagnostics/tool-calls").status_code == 404


@respx.mock
def test_events_are_forwarded_with_resume_id():
    session = str(uuid.uuid4())
    route = respx.get(HOSTED + f"/demo/live/{session}").mock(
        return_value=httpx.Response(
            200, text="event: final\ndata: {}\n\n", headers={"content-type": "text/event-stream"}
        )
    )
    response = client().get(f"/demo/live/{session}", headers={"Last-Event-ID": "12"})
    assert response.text == "event: final\ndata: {}\n\n"
    assert route.calls.last.request.headers["last-event-id"] == "12"
