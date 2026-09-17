"""Tests the `test-webhook` CLI command's logic by routing its httpx.post calls through the
real FastAPI app in-process (no real socket/port needed), so the actual auth dependency and
handler dispatch run for real — this is the thing the command exists to catch before a live
Vapi call hits a misconfigured server.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from collections_agent import cli as cli_module
from collections_agent.webhooks import handlers as handlers_module
from collections_agent.webhooks import server as server_module
from collections_agent.webhooks.server import app

SECRET = "test-secret"
BASE_URL = "http://localhost:8000"


@pytest.fixture(autouse=True)
def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("VAPI_SERVER_SECRET", SECRET)
    monkeypatch.setattr(handlers_module, "TOOL_CALL_LOG_PATH", tmp_path / "tool_calls.jsonl")
    monkeypatch.setattr(server_module, "WEBHOOK_REQUEST_LOG_PATH", tmp_path / "webhook_requests.jsonl")


@pytest.fixture
def _route_httpx_through_app(monkeypatch):
    test_client = TestClient(app)

    def fake_post(url: str, json=None, headers=None, timeout=None):
        path = url.replace(BASE_URL, "")
        return test_client.post(path, json=json, headers=headers)

    monkeypatch.setattr(httpx, "post", fake_post)
    return test_client


def test_test_webhook_passes_all_checks(_route_httpx_through_app):
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["test-webhook"])

    assert result.exit_code == 0, result.output
    assert "wrong secret correctly rejected" in result.output
    assert "All checks passed." in result.output
    for tool_name in (
        "lookup_invoices",
        "record_ptp",
        "log_dispute",
        "log_payment_claim",
        "send_document",
        "schedule_callback",
        "mark_opt_out",
    ):
        assert f"[PASS] {tool_name}" in result.output


def test_test_webhook_requires_secret_configured(monkeypatch, _route_httpx_through_app):
    monkeypatch.delenv("VAPI_SERVER_SECRET", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["test-webhook"])
    assert result.exit_code == 1
