from __future__ import annotations

import json

import httpx
import pytest
import respx
from httpx import Response

from collections_agent.voice.vapi_client import VAPI_BASE_URL, VapiClient


@respx.mock
def test_create_assistant_posts_payload_and_returns_json():
    route = respx.post(f"{VAPI_BASE_URL}/assistant").mock(return_value=Response(200, json={"id": "asst-1"}))
    client = VapiClient(api_key="key-123")

    result = client.create_assistant({"name": "collections-agent-v1"})

    assert route.called
    assert result == {"id": "asst-1"}
    sent_auth = route.calls[0].request.headers["authorization"]
    assert sent_auth == "Bearer key-123"


@respx.mock
def test_start_call_includes_overrides_when_given():
    respx.post(f"{VAPI_BASE_URL}/call").mock(return_value=Response(200, json={"id": "call-1"}))
    client = VapiClient(api_key="key-123")

    result = client.start_call(
        assistant_id="asst-1",
        phone_number_id="phone-1",
        customer_number="+911234567890",
        assistant_overrides={"model": {"messages": [{"role": "system", "content": "hi"}]}},
    )

    assert result == {"id": "call-1"}
    sent_body = respx.calls[-1].request.content
    payload = json.loads(sent_body)
    assert payload["assistantOverrides"]["model"]["messages"][0]["content"] == "hi"


@respx.mock
def test_get_call_returns_json():
    respx.get(f"{VAPI_BASE_URL}/call/call-1").mock(
        return_value=Response(200, json={"id": "call-1", "status": "ended"})
    )
    client = VapiClient(api_key="key-123")

    result = client.get_call("call-1")

    assert result["status"] == "ended"


@respx.mock
def test_get_assistant_returns_json():
    respx.get(f"{VAPI_BASE_URL}/assistant/asst-1").mock(
        return_value=Response(200, json={"id": "asst-1", "server": {"url": "https://x.example.com/hook"}})
    )
    client = VapiClient(api_key="key-123")

    result = client.get_assistant("asst-1")

    assert result["server"]["url"] == "https://x.example.com/hook"


@respx.mock
def test_raises_on_http_error():
    respx.post(f"{VAPI_BASE_URL}/assistant").mock(return_value=Response(500))
    client = VapiClient(api_key="key-123")

    with pytest.raises(httpx.HTTPStatusError):
        client.create_assistant({})
