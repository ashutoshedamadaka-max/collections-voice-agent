"""Runs the scenario set through the simulator and prints the report for `sim-eval`.

Kept separate from simulate.py (the mechanics) so the mechanics stay testable without any
printing, saving, or settings.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from collections_agent.eval.sim_scenarios import (
    MaterializedScenario,
    ScenarioDefaults,
    load_scenarios,
    materialize,
    today_utc,
)
from collections_agent.eval.simulate import (
    PRICES_PER_MILLION,
    SIM_RUNS_DIR,
    BudgetExceeded,
    BudgetUsageSink,
    CostMeter,
    compare_postcall,
    estimate_tokens,
    openai_tools,
    run_checks,
    run_conversation,
    to_transcript,
)
from collections_agent.postcall.openai_client import SPECIALIST_MODEL
from collections_agent.voice.prompt_template import render_system_prompt

# Per-scenario token budget used only for the dry-run estimate: ~8 agent requests that each
# re-send the system prompt, tools and a growing history; ~8 short customer requests; 5
# post-call specialist requests. Deliberately on the high side.
_EST_AGENT_REQUESTS = 9
_EST_CUSTOMER_REQUESTS = 8
_EST_HISTORY_TOKENS = 900


def estimate_run_cost(
    scenarios: list[MaterializedScenario],
    company_name: str,
    agent_model: str,
    customer_model: str,
    postcall: bool,
) -> float:
    a_in, _, a_out = CostMeter.prices(agent_model)
    c_in, _, c_out = CostMeter.prices(customer_model)
    s_in, _, s_out = CostMeter.prices(SPECIALIST_MODEL)
    tools_tokens = estimate_tokens(openai_tools())
    total = 0.0
    for ms in scenarios:
        prompt_tokens = estimate_tokens(render_system_prompt(ms.context_pack, company_name, ms.as_of))
        agent_in = _EST_AGENT_REQUESTS * (prompt_tokens + tools_tokens + _EST_HISTORY_TOKENS)
        total += (agent_in * a_in + _EST_AGENT_REQUESTS * 80 * a_out) / 1_000_000
        total += (_EST_CUSTOMER_REQUESTS * 900 * c_in + _EST_CUSTOMER_REQUESTS * 40 * c_out) / 1_000_000
        if postcall:
            total += (5 * 2500 * s_in + 5 * 300 * s_out) / 1_000_000
    return total


def _select(defaults: ScenarioDefaults, all_scenarios: list, only: list[str] | None) -> list:
    if not only:
        return all_scenarios
    known = {s.id for s in all_scenarios}
    unknown = [i for i in only if i not in known]
    if unknown:
        raise typer.BadParameter(
            f"unknown scenario id(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
        )
    return [s for s in all_scenarios if s.id in only]


def _run_postcall_for(
    ms: MaterializedScenario, convo: Any, meter: CostMeter, api_key: str, threshold: float
) -> tuple[Any | None, str | None]:
    from collections_agent.postcall.pipeline import run_postcall

    transcript = to_transcript(convo)
    usages = BudgetUsageSink(meter)
    try:
        analysis = run_postcall(
            call_id=transcript.call_id,
            account_id=ms.account.account_id,
            transcript=transcript,
            invoices=ms.invoices,
            as_of=ms.as_of,
            api_key=api_key,
            confidence_threshold=threshold,
            usage_sink=usages,
        )
        return analysis, None
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - caller saves and stops on harness errors
        return None, f"{type(e).__name__}: {e}"


def run_sim_eval(
    only: list[str] | None,
    agent_model: str,
    customer_model: str,
    max_usd: float,
    max_turns: int,
    postcall: bool,
    dry_run: bool,
    temperature: float,
) -> None:
    from collections_agent.config import get_settings

    settings = get_settings()
    for model in {agent_model, customer_model, SPECIALIST_MODEL}:
        if model not in PRICES_PER_MILLION:
            raise typer.BadParameter(f"no price on file for {model!r}; add it to PRICES_PER_MILLION first")

    defaults, all_scenarios = load_scenarios()
    chosen = _select(defaults, all_scenarios, only)
    as_of = today_utc()
    materialized = [materialize(s, defaults, as_of) for s in chosen]

    estimate = estimate_run_cost(materialized, settings.company_name, agent_model, customer_model, postcall)
    typer.echo(
        f"{len(materialized)} scenario(s) | agent {agent_model} | customer {customer_model} | "
        f"post-call {'on' if postcall else 'off'} | estimated cost ~${estimate:.2f} | cap ${max_usd:.2f}"
    )
    if dry_run:
        typer.echo("Dry run: nothing was sent to OpenAI.")
        return
    if not settings.openai_api_key:
        typer.echo("Set OPENAI_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=45)
    meter = CostMeter(max_usd=max_usd)
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = SIM_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    from collections_agent.eval.provenance import fingerprint

    manifest = {
        **fingerprint(),
        "run_id": run_id,
        "as_of": str(as_of),
        "agent_model": agent_model,
        "customer_model": customer_model,
        "specialist_model": SPECIALIST_MODEL,
        "max_usd": max_usd,
        "max_turns": max_turns,
        "temperature": temperature,
        "postcall": postcall,
        "prices_per_million": PRICES_PER_MILLION,
        "selected_scenarios": [m.scenario.id for m in materialized],
        "materialized_context": [m.context_pack.model_dump(mode="json") for m in materialized],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results: list[dict[str, Any]] = []
    for ms in materialized:
        typer.echo(f"\n=== {ms.scenario.id}: {ms.scenario.title}")
        convo = run_conversation(
            ms,
            client,
            meter,
            run_dir,
            settings.company_name,
            agent_model=agent_model,
            customer_model=customer_model,
            max_agent_turns=max_turns,
            temperature=temperature,
        )
        checks = run_checks(ms, convo)
        agent_ok = all(c.passed for c in checks if c.must_pass)

        postcall_fields: dict[str, Any] = {}
        postcall_error = None
        budget_stopped = convo.ended_reason == "budget"
        if postcall and convo.ended_reason not in ("budget", "error"):
            try:
                analysis, postcall_error = _run_postcall_for(
                    ms, convo, meter, settings.openai_api_key, settings.confidence_threshold
                )
            except BudgetExceeded as e:
                analysis, postcall_error = None, str(e)
                budget_stopped = True
            if analysis is not None:
                postcall_fields = compare_postcall(ms, analysis)

        for c in checks:
            mark = "PASS" if c.passed else ("FAIL" if c.must_pass else "note")
            typer.echo(f"  [{mark}] {c.name}" + (f" — {c.detail}" if c.detail and not c.passed else ""))
        for name, f in postcall_fields.items():
            mark = "agree" if f["agree"] else "DIFF "
            typer.echo(f"  [post-call {mark}] {name}: expected {f['expected']}, pipeline {f['pipeline']}")
        if postcall_error:
            typer.echo(f"  [post-call error] {postcall_error}")
        typer.echo(f"  cost ${convo.cost_usd:.4f} (agent + customer) | running total ${meter.spent_usd:.4f}")

        result = {
            "scenario": ms.scenario.id,
            "title": ms.scenario.title,
            "agent_passed_must_haves": agent_ok,
            "ended_reason": convo.ended_reason,
            "error": convo.error,
            "checks": [asdict(c) for c in checks],
            "postcall": postcall_fields,
            "postcall_expected": any(
                value is not None for value in ms.scenario.expect.postcall.model_dump().values()
            ),
            "postcall_error": postcall_error,
            "turns": convo.turns,
            "tool_calls": [asdict(c) for c in convo.tool_calls],
            "cost_usd": convo.cost_usd,
        }
        results.append(result)
        (run_dir / f"{ms.scenario.id}.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        if budget_stopped:
            typer.echo("\nSpending cap reached — stopping. Results so far are saved.")
            break
        if convo.ended_reason == "error" or postcall_error:
            typer.echo(f"\nHarness stopped on {ms.scenario.id}: {convo.error or postcall_error}")
            _print_summary(results, meter, run_dir, agent_model, postcall)
            raise typer.Exit(code=1)

    _print_summary(results, meter, run_dir, agent_model, postcall)


def _print_summary(
    results: list[dict[str, Any]], meter: CostMeter, run_dir: Path, agent_model: str, postcall: bool
) -> None:
    n = len(results)
    if n == 0:
        typer.echo("No scenarios ran.")
        return
    passed = sum(r["agent_passed_must_haves"] for r in results)
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "attempted": n,
                "agent_passed": passed,
                "spent_usd": meter.spent_usd,
                "unsettled_reserved_usd": meter.reserved_usd,
                "requests": meter.requests,
                "results": results,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    typer.echo("\n" + "=" * 72)
    typer.echo(f"AGENT (in-call), model {agent_model}: {passed}/{n} scenarios passed every must-have check")
    if postcall:
        assessed = [r for r in results if r.get("postcall_expected", bool(r["postcall"]))]
        end_to_end = sum(
            r["agent_passed_must_haves"]
            and bool(r["postcall"])
            and not r["postcall_error"]
            and all(f["agree"] for f in r["postcall"].values())
            for r in assessed
        )
        typer.echo(
            f"END-TO-END text checks: {end_to_end}/{len(assessed)} scenarios with post-call expectations "
            "passed agent and post-call expectations"
        )
        typer.echo(
            f"  {n - len(assessed)} attempted scenario(s) have no post-call expectation; unscored end-to-end."
        )

    tally: dict[str, list[int]] = {}
    for r in results:
        for c in r["checks"]:
            name = c["name"]
            key = (
                "called the expected tool(s) correctly"
                if name.startswith("called ")
                else "avoided forbidden tools"
                if name.startswith("did not call ")
                else name
            )
            ok, total = tally.setdefault(key, [0, 0])
            tally[key] = [ok + int(c["passed"]), total + 1]
    for key, (ok, total) in tally.items():
        typer.echo(f"  {key:<45} {ok}/{total}")

    if postcall:
        agree: dict[str, list[int]] = {}
        upstream = 0
        for r in results:
            if not r["agent_passed_must_haves"]:
                upstream += bool(r["postcall"])
                continue  # the agent itself got it wrong; not a fair test of the post-call pipeline
            for name, f in r["postcall"].items():
                ok, total = agree.setdefault(name, [0, 0])
                agree[name] = [ok + int(f["agree"]), total + 1]
        typer.echo("\nPOST-CALL pipeline vs. scenario expectation (only scenarios where the agent passed):")
        for name, (ok, total) in agree.items():
            typer.echo(f"  {name:<45} {ok}/{total}")
        if upstream:
            typer.echo(
                f"  ({upstream} scenario(s) left out: the agent failed first, so the call itself was wrong)"
            )

    typer.echo(f"\nSpent ${meter.spent_usd:.4f} over {meter.requests} requests. Saved to {run_dir}")
    if meter.reserved_usd > 0.000001:
        typer.echo(
            f"Unsettled request reservations: ${meter.reserved_usd:.4f}; actual additional cost unknown."
        )
    typer.echo(
        "Scope: scripted scenarios, text only, simulated customer. Says nothing about speech-to-text, "
        "text-to-speech, interruptions or latency, and is not real-world accuracy."
    )
