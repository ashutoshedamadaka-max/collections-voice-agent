from __future__ import annotations

import respx
from httpx import Response

from collections_agent.voice.server_reachability import (
    check_server_reachable,
    server_url_from_assistant,
)


def test_server_url_from_assistant_prefers_current_server_field():
    assistant = {"server": {"url": "https://tunnel.example.com/vapi/tool-calls"}, "serverUrl": "https://stale"}
    assert server_url_from_assistant(assistant) == "https://tunnel.example.com/vapi/tool-calls"


def test_server_url_from_assistant_falls_back_to_legacy_field():
    assistant = {"serverUrl": "https://stale.example.com/vapi/tool-calls"}
    assert server_url_from_assistant(assistant) == "https://stale.example.com/vapi/tool-calls"


def test_server_url_from_assistant_none_when_unconfigured():
    assert server_url_from_assistant({}) is None


@respx.mock
def test_check_server_reachable_ok_when_health_responds():
    respx.get("https://tunnel.example.com/health").mock(return_value=Response(200, json={"status": "ok"}))
    assert check_server_reachable("https://tunnel.example.com/vapi/tool-calls") is None


@respx.mock
def test_check_server_reachable_reports_http_error():
    respx.get("https://tunnel.example.com/health").mock(return_value=Response(500))
    reason = check_server_reachable("https://tunnel.example.com/vapi/tool-calls")
    assert reason is not None
    assert "500" in reason


def test_check_server_reachable_reports_connection_failure():
    reason = check_server_reachable("https://this-host-does-not-exist.invalid/vapi/tool-calls", timeout=2.0)
    assert reason is not None
    assert "this-host-does-not-exist.invalid" in reason
