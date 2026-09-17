"""record_ptp must reject a promise date that's invalid or already in the past instead of
silently writing it — a bare day number ("the 28th") without current-date context in the
prompt is what produced a past-dated PTP in testing; see docs/FAILURES.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import time_machine

from collections_agent.webhooks import handlers as handlers_module
from collections_agent.webhooks.handlers import handle_record_ptp

FROZEN_TODAY = "2026-09-16"
# Noon UTC, not a bare date string — time_machine interprets a bare date at local midnight,
# which lands on the previous UTC day in a timezone ahead of UTC (e.g. IST), off by one.
FROZEN_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

VALID_ARGS = {"invoice_ids": ["INV-1"], "amount": 1000, "date": "2026-09-30", "method": "NEFT"}


@pytest.fixture(autouse=True)
def _redirect_log(tmp_path, monkeypatch):
    """Without this, handle_record_ptp's _log_event writes to the real
    fixtures/tool_calls.jsonl — exactly the kind of test pollution that corrupts real debugging
    data, which is what this file exists to guard against in the first place."""
    monkeypatch.setattr(handlers_module, "TOOL_CALL_LOG_PATH", tmp_path / "tool_calls.jsonl")


@time_machine.travel(FROZEN_NOW)
def test_accepts_future_date():
    result = handle_record_ptp(VALID_ARGS)
    assert result["ptp_id"].startswith("PTP-")
    assert "error" not in result


@time_machine.travel(FROZEN_NOW)
def test_accepts_today():
    result = handle_record_ptp({**VALID_ARGS, "date": FROZEN_TODAY})
    assert result["ptp_id"].startswith("PTP-")


@time_machine.travel(FROZEN_NOW)
def test_rejects_past_date():
    result = handle_record_ptp({**VALID_ARGS, "date": "2026-08-28"})
    assert result["error"] == "date_in_the_past"
    assert "ptp_id" not in result
    assert "2026-08-28" in result["message"]
    assert FROZEN_TODAY in result["message"]


@time_machine.travel(FROZEN_NOW)
def test_rejects_malformed_date():
    result = handle_record_ptp({**VALID_ARGS, "date": "the 28th"})
    assert result["error"] == "invalid_date"
    assert "ptp_id" not in result


@time_machine.travel(FROZEN_NOW)
def test_rejects_missing_date():
    args = {k: v for k, v in VALID_ARGS.items() if k != "date"}
    result = handle_record_ptp(args)
    assert result["error"] == "invalid_date"
