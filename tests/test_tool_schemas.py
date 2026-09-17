from __future__ import annotations

from collections_agent.voice.tool_schemas import ALL_TOOLS, TOOL_NAMES


def test_seven_tools_defined():
    assert len(ALL_TOOLS) == 7


def test_tool_names_match_design_doc():
    assert set(TOOL_NAMES) == {
        "lookup_invoices",
        "record_ptp",
        "log_dispute",
        "log_payment_claim",
        "send_document",
        "schedule_callback",
        "mark_opt_out",
    }


def test_every_tool_has_valid_json_schema_shape():
    for tool in ALL_TOOLS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert fn["name"]
        assert fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        for required_field in params.get("required", []):
            assert required_field in params["properties"]


def test_record_ptp_requires_amount_date_method():
    tool = next(t for t in ALL_TOOLS if t["function"]["name"] == "record_ptp")
    required = set(tool["function"]["parameters"]["required"])
    assert required == {"invoice_ids", "amount", "date", "method"}


def test_every_tool_has_a_timeout_fallback_message():
    """An unreachable/slow webhook must produce speech, not dead air — see docs/FAILURES.md."""
    for tool in ALL_TOOLS:
        message_types = {m["type"] for m in tool["messages"]}
        assert "request-response-delayed" in message_types
        assert "request-failed" in message_types
