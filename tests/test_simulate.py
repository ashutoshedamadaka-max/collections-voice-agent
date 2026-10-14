"""Tests for the text-only call simulator (eval/simulate.py, eval/sim_scenarios.py).

Everything here runs against a scripted fake OpenAI client — no network, no spend. The fake
replays canned customer and agent messages so each check can be exercised on a conversation
whose right answer is known in advance.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from collections_agent.eval import simulate
from collections_agent.eval.sim_scenarios import (
    Scenario,
    load_scenarios,
    materialize,
    next_occurrence_of_day,
    spoken_date,
)
from collections_agent.eval.simulate import (
    BudgetExceeded,
    CostMeter,
    compare_postcall,
    run_checks,
    run_conversation,
    to_transcript,
)
from collections_agent.voice.tool_schemas import TOOL_NAMES
from collections_agent.webhooks import handlers

AS_OF = date(2026, 10, 14)


# --- A scripted stand-in for the OpenAI client ------------------------------------------------


def _usage(prompt: int = 1000, completion: int = 50, cached: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


def say(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text, tool_calls=None)


def call_tool(name: str, **arguments) -> SimpleNamespace:
    tc = SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(content=None, tool_calls=[tc])


class FakeClient:
    """Routes each request to the customer or agent script by whether `tools` was passed —
    only the agent's requests carry tools."""

    def __init__(self, customer: list[str], agent: list[SimpleNamespace]) -> None:
        self.customer = list(customer)
        self.agent = list(agent)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        message = self.agent.pop(0) if "tools" in kwargs else say(self.customer.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=_usage())


@pytest.fixture
def scenarios():
    defaults, all_scenarios = load_scenarios()
    return defaults, {s.id: s for s in all_scenarios}


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Belt and braces: even if the simulator's own redirect failed, nothing reaches the real
    fixtures/tool_calls.jsonl during tests."""
    monkeypatch.setattr(handlers, "TOOL_CALL_LOG_PATH", tmp_path / "should_not_be_used.jsonl")
    return tmp_path


def _materialize(scenarios, scenario_id):
    defaults, by_id = scenarios
    return materialize(by_id[scenario_id], defaults, AS_OF)


def _run(ms, client, tmp_path, max_usd=5.0, **kwargs):
    return run_conversation(ms, client, CostMeter(max_usd=max_usd), tmp_path, "Acme Supplies", **kwargs)


def _failed(checks):
    return {c.name: c.detail for c in checks if c.must_pass and not c.passed}


# --- Scenario file ---------------------------------------------------------------------------


def test_every_scenario_loads_and_materializes(scenarios):
    defaults, by_id = scenarios
    assert len(by_id) >= 15
    for scenario in by_id.values():
        ms = materialize(scenario, defaults, AS_OF)
        assert "{" not in ms.customer_brief, f"{scenario.id}: unfilled placeholder"


def test_every_expected_and_forbidden_tool_exists(scenarios):
    _, by_id = scenarios
    for scenario in by_id.values():
        for e in scenario.expect.tools:
            assert e.name in TOOL_NAMES, f"{scenario.id}: unknown tool {e.name}"
        for name in scenario.expect.forbid_tools:
            assert name in TOOL_NAMES, f"{scenario.id}: unknown tool {name}"


def test_an_unset_placeholder_is_a_clear_error(scenarios):
    defaults, _ = scenarios
    broken = Scenario(id="x", title="x", brief="pay on {promise_date}")
    with pytest.raises(ValueError, match="promise_date"):
        materialize(broken, defaults, AS_OF)


def test_next_occurrence_of_day_skips_short_months():
    # From 14 Oct, the next "31st" is 31 Oct; from 1 Nov it's 31 Dec (November has 30 days).
    assert next_occurrence_of_day(31, date(2026, 10, 14)) == date(2026, 10, 31)
    assert next_occurrence_of_day(31, date(2026, 11, 1)) == date(2026, 12, 31)
    assert next_occurrence_of_day(14, date(2026, 10, 14)) == date(2026, 10, 14)


def test_spoken_date():
    assert spoken_date(date(2026, 10, 23)) == "Friday the 23rd of October"
    assert spoken_date(date(2026, 10, 11)) == "Sunday the 11th of October"


# --- Conversations and checks ----------------------------------------------------------------


def test_correct_promise_passes_every_must_have_check(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "promise_full_upi")
    promise = ms.resolved_dates["promise_date"]
    client = FakeClient(
        customer=[
            "Yes, this is Priya.",
            f"On {spoken_date(promise)}.",
            "The full amount.",
            "UPI.",
            "Yes, correct.",
            "Thanks, bye. <END_CALL>",
        ],
        agent=[
            say("Thank you. When will you pay this invoice?"),
            say("Will you pay the full amount, or only part of it?"),
            say("And how will you pay?"),
            say("To confirm: the full amount, on that date, by UPI?"),
            call_tool(
                "record_ptp",
                invoice_ids=[ms.primary_invoice.invoice_number],
                amount=ms.primary_invoice.outstanding(),
                date=promise.isoformat(),
                method="UPI",
            ),
            say("I've recorded your promise. Thank you, goodbye."),
        ],
    )
    convo = _run(ms, client, tmp_path)
    assert convo.ended_reason == "customer_ended"
    assert [c.name for c in convo.tool_calls] == ["record_ptp"]
    assert "ptp_id" in convo.tool_calls[0].result
    assert _failed(run_checks(ms, convo)) == {}


def test_agent_never_calls_record_ptp_and_claims_it_did(scenarios, isolated_log, tmp_path):
    """The failure from docs/FAILURES.md (2026-09-26): the agent says it recorded the promise,
    but no tool call ever happened."""
    ms = _materialize(scenarios, "promise_full_upi")
    client = FakeClient(
        customer=["Yes, Priya here.", "Full amount, next Friday, UPI.", "Bye. <END_CALL>"],
        agent=[say("When will you pay?"), say("I have recorded this payment promise. Goodbye.")],
    )
    failed = _failed(run_checks(ms, _run(ms, client, tmp_path)))
    assert failed["called record_ptp"] == "never called"
    assert "no action claimed without a tool call" in failed


def test_wrong_details_in_the_tool_call_are_reported(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "promise_full_upi")
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[
            call_tool(
                "record_ptp",
                invoice_ids=[ms.primary_invoice.invoice_number],
                amount=50000,
                date=(AS_OF + timedelta(days=30)).isoformat(),
                method="NEFT",
            ),
            say("Done."),
        ],
    )
    detail = _failed(run_checks(ms, _run(ms, client, tmp_path)))["called record_ptp"]
    assert "amount" in detail and "date" in detail and "method" in detail


def test_invoice_match_ignores_punctuation(scenarios, isolated_log, tmp_path):
    """Same leniency the post-call promise matcher has (docs/FAILURES.md, 2026-09-30)."""
    ms = _materialize(scenarios, "promise_full_upi")
    promise = ms.resolved_dates["promise_date"]
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[
            call_tool(
                "record_ptp",
                invoice_ids=["SGP-26-27-0014"],
                amount=204500,
                date=promise.isoformat(),
                method="upi",
            ),
            say("Thank you."),
        ],
    )
    assert "called record_ptp" not in _failed(run_checks(ms, _run(ms, client, tmp_path)))


def test_a_past_date_rejected_by_the_real_handler_counts_as_a_miss(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "promise_full_upi")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[
            call_tool(
                "record_ptp",
                invoice_ids=[ms.primary_invoice.invoice_number],
                amount=204500,
                date=yesterday,
                method="UPI",
            ),
            say("That date has passed. What date will you pay?"),
        ],
    )
    convo = _run(ms, client, tmp_path)
    assert convo.tool_calls[0].result["error"] == "date_in_the_past"
    assert "rejected" in _failed(run_checks(ms, convo))["called record_ptp"]


def test_forbidden_tool_fails(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[
            call_tool(
                "record_ptp",
                invoice_ids=[ms.primary_invoice.invoice_number],
                amount=204500,
                date=(AS_OF + timedelta(days=7)).isoformat(),
                method="UPI",
            ),
            say("Recorded."),
        ],
    )
    assert "did not call record_ptp" in _failed(run_checks(ms, _run(ms, client, tmp_path)))


@pytest.mark.parametrize("spoken", ["204500 rupees", "2,04,500 rupees", "204,500", "invoice 0014"])
def test_account_facts_read_as_digits_are_caught(scenarios, isolated_log, tmp_path, spoken):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(customer=["Yes.", "Bye. <END_CALL>"], agent=[say(f"You owe {spoken}.")])
    assert "no account facts spoken as digits" in _failed(run_checks(ms, _run(ms, client, tmp_path)))


def test_spoken_forms_pass_the_digit_check(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[say("The outstanding amount is two lakh four thousand five hundred rupees.")],
    )
    checks = run_checks(ms, _run(ms, client, tmp_path))
    assert next(c for c in checks if c.name == "no account facts spoken as digits").passed


def test_disclosing_the_amount_to_the_wrong_person_fails(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "wrong_person")
    client = FakeClient(
        customer=["No, this is Ravi from stores.", "Bye. <END_CALL>"],
        agent=[say("It's about two lakh four thousand five hundred rupees overdue.")],
    )
    assert "disclosed nothing to the wrong person" in _failed(run_checks(ms, _run(ms, client, tmp_path)))


def test_promise_on_the_deferred_invoice_fails(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "two_invoices_asks_about_other")
    other = ms.other_invoices[0]
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[
            call_tool(
                "record_ptp",
                invoice_ids=[other.invoice_number],
                amount=other.outstanding(),
                date=(AS_OF + timedelta(days=5)).isoformat(),
                method="UPI",
            ),
            say("Thanks."),
        ],
    )
    assert "only worked the primary invoice" in _failed(run_checks(ms, _run(ms, client, tmp_path)))


def test_hitting_max_turns_fails_call_finished(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(customer=["Yes."] * 3, agent=[say("When will you pay?")] * 3)
    convo = _run(ms, client, tmp_path, max_agent_turns=3)
    assert convo.ended_reason == "max_turns"
    assert "call finished" in _failed(run_checks(ms, convo))


def test_bundled_questions_are_noted_but_do_not_fail_the_scenario(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[say("What amount will you pay? And when? And how?")],
    )
    checks = run_checks(ms, _run(ms, client, tmp_path))
    bundled = next(c for c in checks if c.name == "one question per turn")
    assert not bundled.passed and not bundled.must_pass
    assert _failed(checks) == {}


def test_tool_log_goes_to_the_run_folder_and_is_restored(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "opt_out")
    before = handlers.TOOL_CALL_LOG_PATH
    client = FakeClient(
        customer=["Stop calling.", "Bye. <END_CALL>"],
        agent=[
            call_tool("mark_opt_out", contact_id="ACC-SIM-0001", scope="this_contact"),
            say("Understood."),
        ],
    )
    _run(ms, client, tmp_path)
    assert (tmp_path / "tool_calls.jsonl").exists()
    assert not (isolated_log / "should_not_be_used.jsonl").exists()
    assert handlers.TOOL_CALL_LOG_PATH == before


def test_lookup_invoices_answers_from_the_scenario_not_the_sheet(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(
        customer=["Yes.", "Bye. <END_CALL>"],
        agent=[call_tool("lookup_invoices", account_id="ACC-SIM-0001"), say("Thanks.")],
    )
    convo = _run(ms, client, tmp_path)
    assert convo.tool_calls[0].result["invoices"][0]["invoice_number"] == "SGP/26-27/0014"


def test_agent_requests_use_the_real_prompt_and_tools(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(customer=["Yes.", "Bye. <END_CALL>"], agent=[say("When will you pay?")])
    _run(ms, client, tmp_path)
    agent_request = next(r for r in client.requests if "tools" in r)
    assert "# REFERENCE VALUES" in agent_request["messages"][0]["content"]
    assert {t["function"]["name"] for t in agent_request["tools"]} == set(TOOL_NAMES)
    assert all("messages" not in t for t in agent_request["tools"])  # Vapi-only key stripped
    assert agent_request["messages"][1]["role"] == "assistant"  # the fixed opening line


# --- Spending cap ----------------------------------------------------------------------------


def test_cost_meter_prices_cached_tokens_at_the_discount():
    meter = CostMeter(max_usd=1.0)
    cost = meter.record("gpt-4o-mini", _usage(prompt=1_000_000, completion=0, cached=1_000_000))
    assert cost == pytest.approx(0.075)
    assert meter.requests == 1


def test_cost_meter_refuses_unknown_models():
    with pytest.raises(ValueError, match="No price on file"):
        CostMeter(max_usd=1.0).ensure_room("some-new-model", 100)


def test_cost_meter_stops_before_the_cap_not_after():
    meter = CostMeter(max_usd=0.01, spent_usd=0.0099)
    with pytest.raises(BudgetExceeded):
        meter.ensure_room("gpt-4o", 10_000)


def test_budget_stop_ends_the_conversation_cleanly(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(customer=["Yes."] * 5, agent=[say("When?")] * 5)
    convo = _run(ms, client, tmp_path, max_usd=0.0)
    assert convo.ended_reason == "budget"
    assert client.requests == []  # stopped before sending anything


# --- Post-call comparison --------------------------------------------------------------------


def _analysis(outcome: str = "dispute", has_dispute: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        outcome=SimpleNamespace(outcome=SimpleNamespace(value=outcome)),
        promise=SimpleNamespace(has_promise=False, downgraded_to_soft_commitment=False),
        dispute=SimpleNamespace(has_dispute=has_dispute),
        compliance=SimpleNamespace(
            promised_discount_or_waiver=False,
            threatened_consequences=False,
            verified_authority=True,
            stayed_within_permitted_facts=True,
            misstated_total=False,
        ),
        write_decision=SimpleNamespace(value="auto_write"),
    )


def test_compare_postcall_scores_only_fields_the_scenario_sets(scenarios):
    ms = _materialize(scenarios, "needs_document")  # sets no post-call expectations
    assert compare_postcall(ms, _analysis()) == {}


def test_compare_postcall_outcome_accepts_any_listed_value(scenarios):
    ms = _materialize(scenarios, "dispute_price_above_po")
    result = compare_postcall(ms, _analysis())
    assert all(f["agree"] for f in result.values())
    assert set(result) == {"outcome", "dispute_existed", "promise_captured"}


def test_compare_postcall_flags_a_wrong_outcome(scenarios):
    ms = _materialize(scenarios, "dispute_price_above_po")
    result = compare_postcall(ms, _analysis(outcome="soft_commitment", has_dispute=False))
    assert not result["outcome"]["agree"] and not result["dispute_existed"]["agree"]


def test_to_transcript_uses_the_real_payload_roles(scenarios, isolated_log, tmp_path):
    ms = _materialize(scenarios, "vague_next_week")
    client = FakeClient(customer=["Yes.", "Bye. <END_CALL>"], agent=[say("When will you pay?")])
    transcript = to_transcript(_run(ms, client, tmp_path))
    assert {t.role for t in transcript.turns} == {"bot", "user"}
    assert transcript.call_id == "sim-vague_next_week"


def test_simulator_module_has_prices_for_its_default_models():
    for model in ("gpt-4o-mini", "gpt-4o"):
        assert model in simulate.PRICES_PER_MILLION


# --- The sim-eval command, end to end with a fake client -------------------------------------


def test_sim_eval_command_runs_and_saves_results(monkeypatch, tmp_path, isolated_log):
    import openai
    from typer.testing import CliRunner

    from collections_agent import cli
    from collections_agent.config import Settings
    from collections_agent.eval import sim_report

    class EndsImmediately(FakeClient):
        def __init__(self, **_kwargs) -> None:
            super().__init__(customer=["Wrong number, sorry. <END_CALL>"] * 50, agent=[])

    monkeypatch.setattr(openai, "OpenAI", EndsImmediately)
    monkeypatch.setattr(sim_report, "SIM_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("collections_agent.config.get_settings", lambda: Settings(openai_api_key="test"))

    result = CliRunner().invoke(cli.app, ["sim-eval", "-s", "opt_out", "-s", "wrong_person", "--no-postcall"])
    assert result.exit_code == 0, result.output
    assert "0/2 scenarios passed every must-have check" in result.output
    saved = sorted(p.name for p in (tmp_path / "runs").glob("*/*.json"))
    assert saved == ["opt_out.json", "wrong_person.json"]


def test_sim_eval_dry_run_sends_nothing(monkeypatch):
    import openai
    from typer.testing import CliRunner

    from collections_agent import cli

    def explode(*_args, **_kwargs):
        raise AssertionError("dry run must not create an OpenAI client")

    monkeypatch.setattr(openai, "OpenAI", explode)
    result = CliRunner().invoke(cli.app, ["sim-eval", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "estimated cost" in result.output and "Dry run" in result.output


def test_sim_eval_rejects_an_unknown_scenario_id():
    from typer.testing import CliRunner

    from collections_agent import cli

    result = CliRunner().invoke(cli.app, ["sim-eval", "-s", "no_such_scenario", "--dry-run"])
    assert result.exit_code != 0
