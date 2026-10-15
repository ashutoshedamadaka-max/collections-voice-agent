"""Regression evidence for invalid labels, attribution and action reliability. No API calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from collections_agent.eval.labels import HumanLabel, load_labels, save_label
from collections_agent.eval.simulate import BudgetExceeded, CostMeter, SimToolCall, _unbacked_claim
from collections_agent.followthrough.job import check_ptp, run_followthrough
from collections_agent.metrics.rollup import compute_metrics
from collections_agent.models.domain import PTP, CallLogEntry, ExceptionEntry, Payment, PromiseExtraction
from collections_agent.postcall.specialists.promise import validate_promise_facts
from collections_agent.sheets.readers import read_ptps
from collections_agent.sheets.writers import ensure_all_tabs, write_ptps
from collections_agent.webhooks.operations import (
    complete_action,
    dispatch_scoped,
    list_actions,
    register_call,
    suppressed,
)


def promise(identifier="P1", **overrides):
    return PTP(
        **{
            **dict(
                ptp_id=identifier,
                account_id="A",
                invoice_ids=["I"],
                amount_promised=100,
                promised_date=date(2026, 9, 1),
                payment_method="UPI",
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
                captured_by="test",
                confidence=1,
                call_id=identifier,
            ),
            **overrides,
        }
    )


def payment(**overrides):
    return Payment(
        **{**dict(invoice_id="I", amount_paid=100, paid_date=date(2026, 9, 1), method="UPI"), **overrides}
    )


def test_pre_promise_and_same_day_money_do_not_fulfill_promise():
    for paid_on in (date(2026, 7, 1), date(2026, 8, 1)):
        result = check_ptp(promise(), [payment(paid_date=paid_on)], date(2026, 9, 5))
        assert result.status == "broken"
        assert result.payment_allocations == {}


def test_one_payment_cannot_fulfill_two_promises_across_runs(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    write_ptps(fake_sheets_backend, [promise()])
    source = SimpleNamespace(list_payments=lambda: [payment(), payment()])  # duplicate import
    run_followthrough(fake_sheets_backend, source, date(2026, 9, 5))
    write_ptps(fake_sheets_backend, [promise("P2")])
    run_followthrough(fake_sheets_backend, source, date(2026, 9, 6))
    rows = {p.ptp_id: p for p in read_ptps(fake_sheets_backend)}
    assert rows["P1"].status == "kept"
    assert rows["P2"].status == "broken"
    assert sum(sum(p.payment_allocations.values()) for p in rows.values()) == 100


def test_allocation_order_is_stable_and_partial_cash_is_measured(fake_sheets_backend):
    ensure_all_tabs(fake_sheets_backend)
    write_ptps(fake_sheets_backend, [promise("P2"), promise("P1")])
    run_followthrough(
        fake_sheets_backend,
        SimpleNamespace(list_payments=lambda: [payment(amount_paid=150)]),
        date(2026, 9, 5),
    )
    rows = read_ptps(fake_sheets_backend)
    assert {p.ptp_id: p.status for p in rows} == {"P1": "kept", "P2": "partial"}
    metrics = compute_metrics([], rows, [], [], date(2026, 9, 5))
    assert metrics.promise_to_pay_kept_rate == 0.5
    assert metrics.attributed_recovery_rate == 0.75
    assert metrics.attributed_payment_value == 150


def test_legacy_resolved_promises_are_not_reported_as_validated_cash():
    metrics = compute_metrics([], [promise(status="kept")], [], [], date(2026, 9, 5))
    assert metrics.promise_to_pay_kept_rate is None
    assert metrics.attributed_recovery_rate is None
    assert metrics.legacy_unverified_promises == 1


@pytest.mark.parametrize("amount", [-1, 0, float("nan"), float("inf")])
def test_invalid_promise_amounts_are_rejected(make_invoice, amount):
    inv = make_invoice("A")
    extract = PromiseExtraction(
        has_promise=True,
        amount=amount,
        promised_date=date(2030, 1, 1),
        method="UPI",
        invoice_ids=[inv.invoice_number],
        confidence=1,
    )
    assert validate_promise_facts(extract, [inv], date(2026, 1, 1)).downgraded_to_soft_commitment


def test_reference_must_be_unique_and_returns_canonical_id(make_invoice):
    inv = make_invoice("A")
    extract = PromiseExtraction(
        has_promise=True,
        amount=1,
        promised_date=date(2030, 1, 1),
        method="UPI",
        invoice_ids=[inv.invoice_number],
        confidence=1,
    )
    assert validate_promise_facts(extract, [inv], date(2026, 1, 1)).invoice_ids == [inv.invoice_id]
    for invoices, refs in (
        ([inv], [inv.invoice_number, "fabricated"]),
        ([inv, inv.model_copy(update={"invoice_id": "other"})], [inv.invoice_number]),
    ):
        result = validate_promise_facts(
            extract.model_copy(update={"invoice_ids": refs}), invoices, date(2026, 1, 1)
        )
        assert result.downgraded_to_soft_commitment


def test_delayed_duplicate_reviews_use_call_cohort():
    call = CallLogEntry(
        call_id="C",
        account_id="A",
        called_at=datetime(2026, 9, 1, tzinfo=UTC),
        outcome="dispute",
        duration_seconds=60,
    )
    exception = ExceptionEntry(
        call_id="C",
        account_id="A",
        reason="uncertain",
        supervisor_notes="x",
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    row = compute_metrics([call], [], [], [exception, exception], date(2026, 9, 1))
    assert row.human_review_rate == 1
    assert row.record_review_calls == 1


def test_random_legacy_labels_cannot_validate_and_v2_requires_evidence(tmp_path):
    old = dict(
        call_id="C",
        outcome="dispute",
        promise_captured=False,
        dispute_existed=True,
        compliance_violated=False,
        write_decision="exception_queue",
        labeled_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        HumanLabel(**old)
    with pytest.raises(ValidationError, match="requires"):
        HumanLabel(
            call_id="C",
            reviewer="human",
            input_sha256="a" * 64,
            assessability="reviewed",
            outcome="dispute",
            labeled_at=datetime.now(UTC),
        )
    label = HumanLabel(
        call_id="C",
        reviewer="human",
        input_sha256="a" * 64,
        assessability="uncertain",
        labeled_at=datetime.now(UTC),
    )
    path = tmp_path / "labels_v2.json"
    save_label(label, path)
    save_label(label.model_copy(update={"notes": "review again"}), path)
    assert load_labels(path)["C"].write_decision is None
    assert json.loads(path.with_suffix(".history.jsonl").read_text())["notes"] == ""


@pytest.mark.parametrize(
    "prior",
    [
        [],
        [SimToolCall(0, "log_dispute", {}, {"dispute_id": "D"})],
        [SimToolCall(0, "schedule_callback", {}, {"error": "failed"})],
    ],
)
def test_unrelated_or_failed_action_does_not_back_callback_claim(prior):
    assert _unbacked_claim("I have scheduled a callback.", 2, prior)
    assert _unbacked_claim("I'll arrange for a follow-up. Please expect a call from our team soon.", 2, prior)


def test_successful_relevant_action_backs_claim_but_future_action_does_not():
    action = SimToolCall(2, "schedule_callback", {}, {"callback_id": "C"})
    assert not _unbacked_claim("I have scheduled a callback.", 2, [action])
    assert _unbacked_claim("I have scheduled a callback.", 1, [action])


def test_concurrent_budget_reservations_cannot_overspend():
    meter = CostMeter(max_usd=0.001)

    def reserve():
        try:
            return meter.reserve("gpt-4o-mini", "small", 500)
        except BudgetExceeded:
            return 0

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: reserve(), range(5)))
    assert sum(bool(r) for r in results) == 1
    assert meter.reserved_usd <= meter.max_usd
    with pytest.raises(RuntimeError, match="Missing usage"):
        meter.settle("gpt-4o-mini", None, max(results))
    assert meter.reserved_usd > 0


@pytest.fixture
def scope(tmp_path, make_invoice):
    path = tmp_path / "operations.db"
    inv = make_invoice("A")
    register_call(path, "C", "A", "phone-A", [inv])
    return path, inv


def test_durable_opt_out_uses_trusted_contact_and_deduplicates(scope):
    path, _ = scope
    args = {"contact_id": "model-hallucinated-id", "scope": "this_contact"}
    first = dispatch_scoped(path, "C", "T1", "mark_opt_out", args)
    assert first["completed"]
    assert suppressed(path, "A", "phone-A")
    assert not suppressed(path, "B", "phone-A")
    assert dispatch_scoped(path, "C", "T1", "mark_opt_out", args) == first
    assert len(list_actions(path)) == 1
    assert "error" in dispatch_scoped(path, "C", "T1", "mark_opt_out", {**args, "scope": "whole_account"})


def test_callbacks_are_pending_until_operator_records_completion(scope):
    path, _ = scope
    result = dispatch_scoped(path, "C", "T1", "schedule_callback", {"reason": "billing question"})
    assert result["status"] == "queued" and not result["completed"]
    assert list_actions(path)[0]["owner"] == "collections_ops"
    with pytest.raises(ValueError):
        complete_action(path, result["receipt_id"], "")
    complete_action(path, result["receipt_id"], "Operator called; resolved invoice question")
    assert list_actions(path)[0]["status"] == "completed"


def test_tools_cannot_cross_accounts_or_accept_unregistered_calls(scope):
    path, inv = scope
    for cid, args in (("missing", {"account_id": "A"}), ("C", {"account_id": "B"})):
        assert "error" in dispatch_scoped(path, cid, "T", "lookup_invoices", args)
    assert "error" in dispatch_scoped(
        path, "C", "T", "send_document", {"invoice_id": "OTHER", "doc_type": "invoice"}
    )
    args = {
        "invoice_ids": [inv.invoice_id],
        "amount": -1,
        "date": str(date.today() + timedelta(days=2)),
        "method": "UPI",
    }
    assert "error" in dispatch_scoped(path, "C", "T", "record_ptp", args)
    assert list_actions(path) == []


def test_simulation_does_not_touch_operational_store(tmp_path, monkeypatch):
    from collections_agent.eval.simulate import _isolated_tool_log
    from collections_agent.webhooks.handlers import dispatch

    monkeypatch.setenv("OPERATIONS_DB_PATH", str(tmp_path / "production.db"))
    with _isolated_tool_log(tmp_path / "simulation"):
        dispatch("mark_opt_out", {"contact_id": "test", "scope": "whole_account"})
    assert not (tmp_path / "production.db").exists()


def test_queue_metrics_separate_accepted_and_completed_work(scope):
    from collections_agent.webhooks.operations import operation_metrics

    path, _ = scope
    dispatch_scoped(path, "C", "T", "schedule_callback", {"reason": "invoice question"})
    metrics = operation_metrics(path)
    assert metrics["followups_accepted"] == 1
    assert metrics["followups_completed"] == 0
    assert metrics["pending_followups"] == 1
    assert metrics["due_followup_sla_rate"] is None


def test_concurrent_duplicate_deliveries_create_one_receipt(scope):
    path, _ = scope
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda _: dispatch_scoped(
                    path, "C", "same-tool-call", "schedule_callback", {"reason": "invoice question"}
                ),
                range(3),
            )
        )
    assert results[0] == results[1] == results[2]
    assert len(list_actions(path)) == 1


def test_unregistered_webhook_and_malformed_arguments_fail_closed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from collections_agent.webhooks import server
    from collections_agent.webhooks.auth import VAPI_SECRET_HEADER
    from collections_agent.webhooks.server import app

    monkeypatch.setenv("VAPI_SERVER_SECRET", "test")
    monkeypatch.setenv("OPERATIONS_DB_PATH", str(tmp_path / "operations.db"))
    monkeypatch.setattr(server, "WEBHOOK_REQUEST_LOG_PATH", tmp_path / "requests.jsonl")
    monkeypatch.setattr("collections_agent.webhooks.handlers.TOOL_CALL_LOG_PATH", tmp_path / "tools.jsonl")
    client = TestClient(app)
    for arguments in ({"reason": "call back"}, "{not-json"):
        response = client.post(
            "/vapi/tool-calls",
            headers={VAPI_SECRET_HEADER: "test"},
            json={
                "message": {
                    "call": {"id": "unregistered"},
                    "toolCallList": [{"id": "T", "name": "schedule_callback", "arguments": arguments}],
                }
            },
        )
        assert response.status_code == 200
        assert "error" in response.json()["results"][0]
    assert list_actions(tmp_path / "operations.db") == []
