"""Compares the real post-call pipeline's output against independently-recorded human labels
for the fixture calls in `fixtures/raw/`.

This measures agreement with one human labeller on a small, mostly-synthetic fixture set — nine
calls at most, as of this writing. It is not a kept rate, not an accuracy estimate at production
volume, and the report must never be read as implying either. See `docs/eval.md`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import typer

from collections_agent.config import get_settings
from collections_agent.eval.labels import HumanLabel, load_labels
from collections_agent.models.domain import ComplianceReview, Invoice, PostCallAnalysis
from collections_agent.postcall.pipeline import run_postcall
from collections_agent.postcall.transcript import RAW_DIR, account_facts_from_system_prompt, parse_transcript
from collections_agent.sheets.client import load_accounts_and_invoices

PIPELINE_CACHE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "eval" / "pipeline_runs"

# Which real account each fixture call belongs to. "historical" means the account no longer
# exists in the current dataset (docs/FAILURES.md, "fixtures coupled to generated data went
# stale silently") — account/invoice facts for those four are recovered from the call's own
# embedded system prompt instead (postcall/transcript.py::account_facts_from_system_prompt),
# not from a live Sheet row. Verified by hand against fixtures/fake_ar_data.json; update this
# if fixtures/raw/ ever gains or loses a file.
CALL_ACCOUNTS: dict[str, str] = {
    "01a0edd7-f19a-7000-956e-08f20925f97a": "ACC-0019",
    "01a0dd63-2149-7661-b588-6cc3e5f6b400": "ACC-0019",
    "01a0dd55-f249-7224-aa92-2361d35affb7": "ACC-0001",
    "01a0afd5-44e2-7000-859b-01f47f6dc3af": "ACC-0001",
    "01a0dd4f-821d-7000-8821-dbbcf2435e91": "ACC-0001",
    "01a0a883-a8b4-7667-be53-3cb44b5377aa": "historical",
    "01a0a8b0-8c17-7557-a077-16bf9def6431": "historical",
    "01a0a8c5-3a62-7223-b72b-d20d526151e9": "historical",
    "01a0a9c4-19b8-7ddd-b81b-4db85947135e": "historical",
}

FIELD_LABELS: dict[str, str] = {
    "outcome": "Outcome type",
    "promise_captured": "Promise captured",
    "promise_complete": "Promise complete",
    "dispute_existed": "Dispute existed",
    "compliance_violated": "Compliance violated",
    "write_decision": "Write decision",
}
FIELD_ORDER = list(FIELD_LABELS.keys())


def _compliance_violation(compliance: ComplianceReview) -> bool:
    """Same "hard violation" test the supervisor itself uses (pipeline.py's
    `_hard_compliance_violations`), restated here rather than imported so this file's
    comparisons stay readable as one self-contained definition of what "violated" means."""
    return (
        compliance.promised_discount_or_waiver
        or compliance.threatened_consequences
        or not compliance.verified_authority
        or not compliance.stayed_within_permitted_facts
        or compliance.misstated_total
    )


def _raw_for(call_id: str) -> dict[str, Any]:
    return json.loads((RAW_DIR / f"{call_id}.json").read_text(encoding="utf-8"))


def _invoices_for(call_id: str, raw: dict[str, Any], settings: Any) -> tuple[str, list[Invoice]]:
    account_id = CALL_ACCOUNTS.get(call_id, "historical")
    if account_id == "historical":
        facts = account_facts_from_system_prompt(raw)
        return "ACC-HISTORICAL", facts["invoices"]
    _, all_invoices = load_accounts_and_invoices(settings)
    return account_id, [inv for inv in all_invoices if inv.account_id == account_id]


def _load_or_run_pipeline(call_id: str, refresh: bool) -> PostCallAnalysis:
    cache_path = PIPELINE_CACHE_DIR / f"{call_id}.json"
    if cache_path.exists() and not refresh:
        return PostCallAnalysis.model_validate_json(cache_path.read_text(encoding="utf-8"))

    settings = get_settings()
    raw = _raw_for(call_id)
    transcript = parse_transcript(raw)
    account_id, invoices = _invoices_for(call_id, raw, settings)
    # The call's own date, not today — see demo_replay.py's module docstring for why this
    # matters to the promise validator's future-dated check.
    as_of = transcript.started_at.date() if transcript.started_at else date.today()

    analysis = run_postcall(
        call_id=call_id,
        account_id=account_id,
        transcript=transcript,
        invoices=invoices,
        as_of=as_of,
        api_key=settings.openai_api_key,
        confidence_threshold=settings.confidence_threshold,
    )
    PIPELINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return analysis


def _compare_one(label: HumanLabel, analysis: PostCallAnalysis) -> dict[str, dict[str, Any]]:
    pipeline_complete = analysis.promise.has_promise and not analysis.promise.downgraded_to_soft_commitment
    pipeline_violated = _compliance_violation(analysis.compliance)

    def field(human: Any, pipeline: Any, applicable: bool = True) -> dict[str, Any]:
        return {
            "human": human,
            "pipeline": pipeline,
            "agree": (human == pipeline) if applicable else None,
            "applicable": applicable,
        }

    return {
        "outcome": field(label.outcome.value, analysis.outcome.outcome.value),
        "promise_captured": field(label.promise_captured, analysis.promise.has_promise),
        "promise_complete": field(label.promise_complete, pipeline_complete, applicable=label.promise_captured),
        "dispute_existed": field(label.dispute_existed, analysis.dispute.has_dispute),
        "compliance_violated": field(label.compliance_violated, pipeline_violated),
        "write_decision": field(label.write_decision.value, analysis.write_decision.value),
    }


def _print_summary_table(results: dict[str, dict[str, dict]], n: int) -> None:
    typer.echo("=" * 72)
    typer.echo(f"AGREEMENT WITH HUMAN LABELS (n={n} labeled call{'s' if n != 1 else ''})")
    typer.echo("=" * 72)
    for field_name in FIELD_ORDER:
        applicable = [r[field_name] for r in results.values() if r[field_name]["applicable"]]
        agree_n = sum(1 for f in applicable if f["agree"])
        total = len(applicable)
        pct = (agree_n / total * 100) if total else 0.0
        scope_note = "" if total == n else f"  (n={total} applicable)"
        typer.echo(f"  {FIELD_LABELS[field_name]:<22} {agree_n}/{total} ({pct:.0f}%){scope_note}")
    typer.echo()


def _print_write_decision_breakdown(results: dict[str, dict[str, dict]]) -> None:
    false_holds = []  # human: auto_write, pipeline: exception_queue — unnecessarily held
    false_writes = []  # human: exception_queue, pipeline: auto_write — the dangerous direction
    for call_id, r in results.items():
        f = r["write_decision"]
        if f["agree"]:
            continue
        if f["human"] == "auto_write" and f["pipeline"] == "exception_queue":
            false_holds.append(call_id)
        elif f["human"] == "exception_queue" and f["pipeline"] == "auto_write":
            false_writes.append(call_id)
    typer.echo("Write-decision disagreements:")
    typer.echo(f"  False holds  (should auto-write, pipeline held it for review): {len(false_holds)}")
    for cid in false_holds:
        typer.echo(f"    - {cid}")
    typer.echo(f"  False writes (should hold, pipeline auto-wrote it):            {len(false_writes)}")
    for cid in false_writes:
        typer.echo(f"    - {cid}")
    typer.echo()


def _print_transcript(call_id: str) -> None:
    transcript = parse_transcript(_raw_for(call_id))
    if not transcript.turns:
        typer.echo("(transcript has 0 turns — this call never connected / no audio received)")
        return
    for turn in transcript.turns:
        speaker = "AGENT" if turn.role in ("assistant", "bot") else "CUSTOMER"
        typer.echo(f"{speaker}: {turn.content}")
    if transcript.tool_calls:
        typer.echo("\nTool calls made during the call:")
        for tc in transcript.tool_calls:
            typer.echo(f"  {tc.name}({tc.arguments}) -> {tc.result}")


def _print_disagreements(
    results: dict[str, dict[str, dict]],
    labels: dict[str, HumanLabel],
    analyses: dict[str, PostCallAnalysis],
) -> None:
    disagreeing = [
        call_id
        for call_id, r in results.items()
        if any(f["applicable"] and not f["agree"] for f in r.values())
    ]
    if not disagreeing:
        typer.echo("No disagreements — every labeled call matched on every applicable field.")
        return

    typer.echo("=" * 72)
    typer.echo(f"DISAGREEMENTS IN FULL ({len(disagreeing)} of {len(labels)} labeled calls)")
    typer.echo("=" * 72)
    for call_id in disagreeing:
        label = labels[call_id]
        analysis = analyses[call_id]
        r = results[call_id]

        typer.echo(f"\n--- {call_id} ---\n")
        _print_transcript(call_id)

        typer.echo("\nField-by-field:")
        for field_name in FIELD_ORDER:
            f = r[field_name]
            if not f["applicable"]:
                typer.echo(f"  {FIELD_LABELS[field_name]:<22} n/a (no promise captured)")
                continue
            mark = "match" if f["agree"] else "DISAGREE"
            typer.echo(
                f"  {FIELD_LABELS[field_name]:<22} human={f['human']!r:<16} "
                f"pipeline={f['pipeline']!r:<16} [{mark}]"
            )

        typer.echo(f"\n  Human notes:                 {label.notes or '(none)'}")
        typer.echo(f"  Pipeline outcome.next_action: {analysis.outcome.next_action}")
        typer.echo(f"  Pipeline promise.notes:       {analysis.promise.notes or '(none)'}")
        typer.echo(f"  Pipeline dispute.detail:      {analysis.dispute.detail or '(none)'}")
        typer.echo(f"  Pipeline compliance.notes:    {analysis.compliance.notes or '(none)'}")
        typer.echo(f"  Pipeline supervisor_notes:    {analysis.supervisor_notes or '(none)'}")
        typer.echo(f"  Pipeline exception_reason:    {analysis.exception_reason or '(none)'}")
        typer.echo(f"  Pipeline overall_confidence:  {analysis.overall_confidence:.2f}")


def run_comparison(refresh: bool = False) -> None:
    labels = load_labels()
    all_raw = sorted(p.stem for p in RAW_DIR.glob("*.json"))

    if not labels:
        typer.echo("No labels yet — run `label-calls` first.")
        raise typer.Exit(code=1)

    typer.echo(f"{len(labels)} of {len(all_raw)} fixture calls labeled.")
    missing = [c for c in all_raw if c not in labels]
    if missing:
        typer.echo("Not yet labeled (excluded from this report): " + ", ".join(missing))
    typer.echo()

    results: dict[str, dict[str, dict]] = {}
    analyses: dict[str, PostCallAnalysis] = {}
    for call_id in sorted(labels):
        typer.echo(f"Running pipeline on {call_id}...")
        analysis = _load_or_run_pipeline(call_id, refresh)
        analyses[call_id] = analysis
        results[call_id] = _compare_one(labels[call_id], analysis)
    typer.echo()

    _print_summary_table(results, n=len(labels))
    _print_write_decision_breakdown(results)
    _print_disagreements(results, labels, analyses)
