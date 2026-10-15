"""Single CLI entry point for every manual step in the build (`uv run collections-agent ...`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from collections_agent.config import get_settings
from collections_agent.data.fake_data_gen import FAKE_DATA_PATH, generate_fake_ar_data

app = typer.Typer(help="B2B collections voice agent — build and operate the pipeline.")


@app.command("gen-data")
def gen_data(
    seed: int = 42,
    out: Path = FAKE_DATA_PATH,
) -> None:
    """Generate ~25 fake accounts / ~60 fake invoices (plus a handful of sample disputes) and
    write them to a local JSON file."""
    accounts, invoices, disputes = generate_fake_ar_data(seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "accounts": [a.model_dump(mode="json") for a in accounts],
                "invoices": [i.model_dump(mode="json") for i in invoices],
                "disputes": [d.model_dump(mode="json") for d in disputes],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo(
        f"Wrote {len(accounts)} accounts, {len(invoices)} invoices, "
        f"and {len(disputes)} sample disputes to {out}"
    )


@app.command("seed-sheet")
def seed_sheet(data_file: Path = FAKE_DATA_PATH) -> None:
    """Create the CRM tabs (if missing) in the configured Google Sheet and seed fake AR data."""
    from collections_agent.models.domain import Account, Dispute, Invoice
    from collections_agent.sheets.client import build_sheets_backend
    from collections_agent.sheets.writers import ensure_all_tabs, seed_accounts, seed_invoices, write_disputes

    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_service_account_json:
        typer.echo(
            "Set GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON in .env first "
            "(share the sheet with the service account's client_email as Editor).",
            err=True,
        )
        raise typer.Exit(code=1)

    if not data_file.exists():
        typer.echo(f"{data_file} not found — run `gen-data` first.", err=True)
        raise typer.Exit(code=1)

    raw = json.loads(data_file.read_text(encoding="utf-8"))
    accounts = [Account.model_validate(a) for a in raw["accounts"]]
    invoices = [Invoice.model_validate(i) for i in raw["invoices"]]
    disputes = [Dispute.model_validate(d) for d in raw.get("disputes", [])]

    backend = build_sheets_backend(settings.google_sheet_id, settings.google_service_account_json)
    ensure_all_tabs(backend)
    seed_accounts(backend, accounts)
    seed_invoices(backend, invoices)
    if disputes:
        write_disputes(backend, disputes)
    typer.echo(
        f"Seeded {len(accounts)} accounts, {len(invoices)} invoices, "
        f"and {len(disputes)} disputes into the sheet."
    )


@app.command("serve-webhook")
def serve_webhook(host: str | None = None, port: int | None = None) -> None:
    """Run the local FastAPI server Vapi calls during a live call (tunnel it before pointing Vapi at it)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "collections_agent.webhooks.server:app",
        host=host or settings.webhook_host,
        port=port or settings.webhook_port,
        reload=True,
    )


@app.command("create-assistant")
def create_assistant(server_url: str, match_name: str = "collection agent") -> None:
    """Create (or update) the Vapi assistant pointed at `server_url` (your ngrok/cloudflared tunnel).

    Avoids creating a duplicate: if VAPI_ASSISTANT_ID is already set, that assistant is
    updated. Otherwise, existing assistants are searched (case-insensitive substring) for
    `match_name` — if exactly one match is found, it's updated instead of creating a new one.
    """
    from collections_agent.voice.assistant_config import build_assistant_payload
    from collections_agent.voice.vapi_client import VapiClient

    settings = get_settings()
    if not settings.vapi_api_key:
        typer.echo("Set VAPI_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)
    if not settings.vapi_openai_credential_id:
        typer.echo(
            "Warning: VAPI_OPENAI_CREDENTIAL_ID is not set — the in-call model will bill "
            "Vapi-hosted credits instead of your OpenAI balance. Add your OpenAI key under "
            "Vapi Dashboard -> Provider Keys first if that's not what you want.",
            err=True,
        )

    payload = build_assistant_payload(
        settings.company_name, server_url, openai_credential_id=settings.vapi_openai_credential_id or None
    )
    client = VapiClient(api_key=settings.vapi_api_key)

    target_id = settings.vapi_assistant_id or None
    if not target_id:
        existing = client.list_assistants()
        matches = [a for a in existing if match_name.lower() in (a.get("name") or "").lower()]
        if len(matches) > 1:
            typer.echo(
                f"Found {len(matches)} assistants matching {match_name!r} "
                f"({[a.get('id') for a in matches]}) — ambiguous. "
                "Set VAPI_ASSISTANT_ID in .env to the one you want updated and re-run.",
                err=True,
            )
            raise typer.Exit(code=1)
        if matches:
            target_id = matches[0]["id"]
            typer.echo(f"Found existing assistant {matches[0].get('name')!r} ({target_id}) — updating it.")

    if target_id:
        result = client.update_assistant(target_id, payload)
        typer.echo(f"Updated assistant {result.get('id')}. Set VAPI_ASSISTANT_ID in .env to this value.")
    else:
        result = client.create_assistant(payload)
        typer.echo(f"Created assistant {result.get('id')}. Set VAPI_ASSISTANT_ID in .env to this value.")


@app.command("test-call")
def test_call(dial: bool = False, ignore_calling_hours: bool = False) -> None:
    """Pick the top-priority account off the pre-call queue, render its context pack into the
    real per-call system prompt, and print it. Pass --dial to also write fixtures/test_call.html
    — a static page (Vapi's Web SDK via CDN script tag, no npm/bundler) with that prompt wired
    in via assistantOverrides — served from this same webhook app at /test-call, so the
    dashboard's Talk button (which can't inject overrides) isn't the only way to test a call
    with real context.

    The page starts the call client-side with VAPI_PUBLIC_KEY (never the private
    VAPI_API_KEY) — Vapi's REST /call no longer supports a phone-number-less call server-side,
    it now requires `type: outboundPhoneCall` or `inboundPhoneCall` either way, so this never
    dials a real phone number or the private API.

    --ignore-calling-hours is a testing-only override: it swaps in a fixed noon-IST timestamp
    for the calling-hours suppression check specifically, so you can dry-run/dial outside 9am-
    6pm. It does not touch the suppression engine itself, and every other suppression rule
    (opt-out, wrong-party, open PTP, 90+ bucket, etc.) still applies normally.
    """
    from datetime import UTC, datetime, time
    from zoneinfo import ZoneInfo

    from collections_agent.precall.context_pack import build_context_pack
    from collections_agent.precall.priority import priority_score
    from collections_agent.precall.rules_config import load_priority_weights
    from collections_agent.precall.suppression import is_suppressed
    from collections_agent.sheets.client import load_accounts_and_invoices
    from collections_agent.voice.assistant_config import build_call_overrides
    from collections_agent.voice.prompt_template import render_system_prompt
    from collections_agent.voice.server_reachability import check_server_reachable, server_url_from_assistant
    from collections_agent.voice.test_call_page import render_test_call_page
    from collections_agent.voice.vapi_client import VapiClient

    settings = get_settings()
    if dial and (not settings.vapi_public_key or not settings.vapi_assistant_id or not settings.vapi_api_key):
        typer.echo(
            "Set VAPI_PUBLIC_KEY, VAPI_API_KEY, and VAPI_ASSISTANT_ID in .env first (run "
            "`create-assistant`; the public key is in the Vapi Dashboard under API Keys — "
            "not the private key).",
            err=True,
        )
        raise typer.Exit(code=1)

    if dial:
        # A dead/misconfigured webhook server doesn't fail loudly — Vapi just never gets a
        # tool-call response, which sounds exactly like the agent freezing (docs/FAILURES.md).
        # Check before spending call minutes on a call whose tools can't possibly work.
        client = VapiClient(api_key=settings.vapi_api_key)
        assistant = client.get_assistant(settings.vapi_assistant_id)
        server_url = server_url_from_assistant(assistant)
        if not server_url:
            typer.echo(
                "Refusing to dial: the assistant has no server URL configured, so tool calls "
                "have nowhere to go. Run `create-assistant` after starting a tunnel.",
                err=True,
            )
            raise typer.Exit(code=1)
        unreachable_reason = check_server_reachable(server_url)
        if unreachable_reason:
            typer.echo(
                f"Refusing to dial: {unreachable_reason}\n"
                "The assistant's tool-call webhook is unreachable — a call would proceed but "
                "any tool call (record_ptp, log_dispute, ...) would hang until it times out. "
                "Start a fresh tunnel and re-run `create-assistant`, then try again.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        accounts, invoices = load_accounts_and_invoices(settings)
    except (FileNotFoundError, NotImplementedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e

    weights = load_priority_weights()
    now = datetime.now(UTC)
    as_of = now.date()
    suppression_now = (
        datetime.combine(as_of, time(12, 0), tzinfo=ZoneInfo("Asia/Kolkata")) if ignore_calling_hours else now
    )
    if ignore_calling_hours:
        typer.echo("--ignore-calling-hours: bypassing the calling-hours check only (testing).\n")

    from collections_agent.sheets.client import load_call_history

    ptp_history, disputes, call_log = load_call_history(settings)
    ranked: list[tuple[float, object]] = []
    for account in accounts:
        suppression = is_suppressed(
            account, invoices, call_log=call_log, open_ptps=ptp_history, now=suppression_now
        )
        if suppression.suppressed:
            continue
        score = priority_score(
            account, invoices, ptp_history=ptp_history, call_log=call_log, weights=weights, as_of=as_of
        )
        if score > 0:
            ranked.append((score, account))

    if not ranked:
        typer.echo(
            "No callable accounts right now — every account is suppressed "
            "(check calling hours/timezone, or that fixtures/fake_ar_data.json has data).",
            err=True,
        )
        raise typer.Exit(code=1)

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top_account = ranked[0]

    context_pack = build_context_pack(
        top_account, invoices, ptp_history=ptp_history, open_disputes=disputes, call_log=call_log, as_of=as_of
    )
    prompt = render_system_prompt(context_pack, settings.company_name, current_date=as_of)

    typer.echo(
        f"Top-priority account: {top_account.account_id} ({top_account.customer_name}, score {top_score})"
    )
    typer.echo("\n===== RENDERED SYSTEM PROMPT =====\n")
    typer.echo(prompt)
    typer.echo("\n===================================")

    if not dial:
        typer.echo("\nDry run only — pass --dial to generate a test-call page with this prompt injected.")
        return

    overrides = build_call_overrides(
        context_pack, settings.company_name, settings.vapi_openai_credential_id or None, current_date=as_of
    )
    account_label = f"{top_account.account_id} — {top_account.customer_name} (score {top_score})"
    html = render_test_call_page(
        account_label=account_label,
        system_prompt=prompt,
        public_key=settings.vapi_public_key,
        assistant_id=settings.vapi_assistant_id,
        assistant_overrides=overrides,
    )
    out_path = Path("fixtures/test_call.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    url = f"http://localhost:{settings.webhook_port}/test-call"
    typer.echo(f"\nWrote {out_path}.")
    typer.echo(f"Open this URL in your browser (serve-webhook must be running): {url}")


@app.command("pull-transcripts")
def pull_transcripts(call_id: str) -> None:
    """Fetch one call from Vapi, save the raw payload verbatim to fixtures/raw/, and attempt
    a provisional parse into fixtures/transcripts/. Run within 14 days — Vapi's retention
    limit. The raw payload is always saved even if parsing fails.
    """
    from collections_agent.postcall.transcript import parse_transcript, save_parsed, save_raw
    from collections_agent.voice.vapi_client import VapiClient

    settings = get_settings()
    if not settings.vapi_api_key:
        typer.echo("Set VAPI_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)

    client = VapiClient(api_key=settings.vapi_api_key)
    raw = client.get_call(call_id)
    raw_path = save_raw(call_id, raw)
    typer.echo(f"Saved raw payload to {raw_path}")

    try:
        transcript = parse_transcript(raw)
        parsed_path = save_parsed(transcript)
        typer.echo(
            f"Parsed ({len(transcript.turns)} turns, {len(transcript.tool_calls)} tool calls) "
            f"to {parsed_path}. This parse is provisional — sanity-check it against the raw payload."
        )
    except Exception as e:  # noqa: BLE001 — deliberately broad: parsing is a guess, must never crash the pull
        typer.echo(
            f"Parsing failed ({e}). That's fine — the raw payload is saved; "
            "correct postcall/transcript.py against it and re-run.",
            err=True,
        )


@app.command("fetch-recording")
def fetch_recording(call_id: str) -> None:
    """Mints a fresh, working recording link for a call. Vapi's recording URLs are presigned
    and expire in ~30 minutes, and the bare `recordingUrl` field is never independently
    fetchable (it points at a private storage bucket) — see docs/FAILURES.md. Call_Log stores
    a pointer to this command instead of a link that would already be dead by the time anyone
    reads the row. Run within 14 days — Vapi's retention limit.
    """
    from collections_agent.postcall.transcript import extract_recording_link
    from collections_agent.voice.vapi_client import VapiClient

    settings = get_settings()
    if not settings.vapi_api_key:
        typer.echo("Set VAPI_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)

    client = VapiClient(api_key=settings.vapi_api_key)
    raw = client.get_call(call_id)
    url, expires_at = extract_recording_link(raw)
    if not url:
        typer.echo(f"No recording URL found in call {call_id}'s payload.", err=True)
        raise typer.Exit(code=1)

    typer.echo(url)
    if expires_at:
        typer.echo(f"(expires {expires_at} — re-run this command for a fresh link after that)")


@app.command("run-postcall")
def run_postcall_cmd(
    call_id: str,
    account_id: str = typer.Option(
        ...,
        "--account-id",
        help="Required — Step 5 write-back needs this for every tab it writes, and the "
        "promise validator needs it for the outstanding-balance check.",
    ),
) -> None:
    """Step 4: run the four post-call specialists (outcome, promise, dispute, compliance)
    against a transcript already pulled by `pull-transcripts`, and have the supervisor merge
    them. Costs real OpenAI credit (~$0.01-0.05/call on gpt-4o-mini). Writes
    fixtures/postcall/<call_id>.json; does not touch Sheets (that's `write-back`).
    """
    from datetime import date

    from collections_agent.postcall.pipeline import run_postcall
    from collections_agent.postcall.transcript import PARSED_DIR, Transcript
    from collections_agent.sheets.client import load_accounts_and_invoices

    settings = get_settings()
    if not settings.openai_api_key:
        typer.echo("Set OPENAI_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)

    transcript_path = PARSED_DIR / f"{call_id}.json"
    if not transcript_path.exists():
        typer.echo(f"{transcript_path} not found — run `pull-transcripts {call_id}` first.", err=True)
        raise typer.Exit(code=1)
    transcript = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))

    try:
        _, all_invoices = load_accounts_and_invoices(settings)
        invoices = [inv for inv in all_invoices if inv.account_id == account_id]
    except (FileNotFoundError, NotImplementedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    if not invoices:
        typer.echo(f"No invoices found for account {account_id} — continuing without them.", err=True)

    analysis = run_postcall(
        call_id=call_id,
        account_id=account_id,
        transcript=transcript,
        invoices=invoices,
        as_of=date.today(),
        api_key=settings.openai_api_key,
        confidence_threshold=settings.confidence_threshold,
    )

    out_path = Path("fixtures/postcall") / f"{call_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    typer.echo(f"outcome: {analysis.outcome.outcome.value} (confidence {analysis.outcome.confidence:.2f})")
    typer.echo(
        f"promise: has_promise={analysis.promise.has_promise} complete={analysis.promise.is_complete} "
        f"downgraded_to_soft_commitment={analysis.promise.downgraded_to_soft_commitment} "
        f"(confidence {analysis.promise.confidence:.2f})"
    )
    typer.echo(
        f"dispute: has_dispute={analysis.dispute.has_dispute} (confidence {analysis.dispute.confidence:.2f})"
    )
    typer.echo(
        f"compliance: qa_score={analysis.compliance.qa_score:.2f} "
        f"(confidence {analysis.compliance.confidence:.2f})"
    )
    typer.echo(f"\noverall_confidence: {analysis.overall_confidence:.2f}")
    typer.echo(f"write_decision: {analysis.write_decision.value}")
    if analysis.exception_reason:
        typer.echo(f"exception_reason: {analysis.exception_reason}")
    if analysis.supervisor_notes:
        typer.echo(f"supervisor_notes: {analysis.supervisor_notes}")
    typer.echo(f"\nWrote {out_path}")


@app.command("label-calls")
def label_calls_cmd(
    redo: bool = typer.Option(False, "--redo", help="Re-label calls that already have a label."),
    call_id: str | None = typer.Option(
        None, "--call-id", help="Label (or relabel, with --redo) just this one call."
    ),
) -> None:
    """Record evidence-based independent labels in fixtures/eval/labels_v2.json.

    Transcript facts, expected records and operational follow-up are separate. Uncertain
    fields remain unscored. Original random labels are preserved but excluded. Pipeline
    analysis is hidden; write correctness requires subsequent review of a specific record.

    Resumable: already-labeled calls are skipped by default, so you can stop at any point
    (including with Ctrl-C — whatever you've already answered for completed calls is saved) and
    pick up later by running this again.
    """
    from collections_agent.eval.labels import load_labels
    from collections_agent.postcall.transcript import RAW_DIR

    raw_files = sorted(RAW_DIR.glob("*.json"))
    if not raw_files:
        typer.echo(f"No fixtures in {RAW_DIR}.", err=True)
        raise typer.Exit(code=1)

    if call_id:
        raw_files = [p for p in raw_files if p.stem == call_id]
        if not raw_files:
            typer.echo(f"No fixture found for call_id {call_id!r} in {RAW_DIR}.", err=True)
            raise typer.Exit(code=1)

    existing = load_labels()
    pending = [p for p in raw_files if redo or p.stem not in existing]

    if not pending:
        typer.echo(
            f"All {len(raw_files)} call(s) already labeled. "
            "Pass --redo to relabel, or --call-id to redo just one."
        )
        return

    typer.echo(
        f"{len(pending)} call(s) to label "
        f"({len(existing)} already done out of {len(raw_files)} total fixtures).\n"
    )

    for i, path in enumerate(pending, start=1):
        cid = path.stem
        _label_one_call(cid, i, len(pending))

    typer.echo("All requested calls labeled.")


def _label_one_call(call_id: str, position: int, total: int) -> None:
    from datetime import UTC, datetime

    from collections_agent.eval.labels import HumanLabel, save_label, source_hash
    from collections_agent.models.domain import CallOutcomeType
    from collections_agent.postcall.transcript import RAW_DIR, parse_transcript

    raw = json.loads((RAW_DIR / f"{call_id}.json").read_text(encoding="utf-8"))
    transcript = parse_transcript(raw)

    typer.echo("=" * 72)
    typer.echo(f"[{position}/{total}] {call_id}")
    typer.echo(
        f"ended: {transcript.ended_reason}  duration: {transcript.duration_seconds}s  "
        f"cost: ${transcript.cost_usd}"
    )
    typer.echo("=" * 72)

    if not transcript.turns:
        typer.echo("(transcript has 0 turns — this call never connected / no audio received)")
    for turn_number, turn in enumerate(transcript.turns, start=1):
        speaker = "AGENT" if turn.role in ("assistant", "bot") else "CUSTOMER"
        typer.echo(f"[{turn_number}] {speaker}: {turn.content}")
    if transcript.tool_calls:
        typer.echo("\nTool calls made during the call:")
        for tc in transcript.tool_calls:
            typer.echo(f"  {tc.name}({tc.arguments}) -> {tc.result}")
    typer.echo()

    try:
        reviewer = typer.prompt("Reviewer name/identifier").strip()
        assessability = _prompt_choice("Assessability", ["reviewed", "uncertain", "unusable"])
        evidence = {}

        def record_evidence(name):
            while True:
                quote = typer.prompt(f"Evidence for {name} (turn number/quote and reasoning)").strip()
                if quote:
                    evidence[name] = quote
                    return

        def judgment(name, question):
            answer = _prompt_choice(question, ["yes", "no", "uncertain", "not_applicable"])
            if answer in ("uncertain", "not_applicable"):
                return None
            record_evidence(name)
            return answer == "yes"

        outcome_values = [o.value for o in CallOutcomeType] + ["uncertain"]
        typer.echo("Outcome type:")
        for idx, v in enumerate(outcome_values, start=1):
            typer.echo(f"  {idx}. {v}")
        outcome = _prompt_choice("Outcome", outcome_values)
        if outcome != "uncertain":
            record_evidence("outcome")

        promise_captured = judgment(
            "promise_captured", "Did the caller make a future payment promise (not a past payment claim)?"
        )
        promise_complete = None
        if promise_captured:
            promise_complete = judgment(
                "promise_complete",
                "Was it complete (specific amount, date, and method; date strictly in the "
                "future; amount within the outstanding balance; method valid)?",
            )

        dispute_existed = judgment("dispute_existed", "Did a dispute exist on this call?")
        compliance_violated = judgment(
            "compliance_violated",
            "Was any compliance rule actually broken (discount/waiver offered, consequences "
            "threatened, authority not verified before stating amounts, a fact stated beyond "
            "what was given, or a misstated total)?",
        )

        typer.echo("A correct record can be saved even when a human must resolve the business issue.")
        expected_records = typer.prompt("Expected records and supported fields (or 'uncertain')")
        operational_followup = judgment(
            "operational_followup", "Does the business case require human follow-up?"
        )
        followup_reason = typer.prompt("Follow-up owner/reason/due window (or N/A)")
        typer.echo(
            "Write correctness is unscored until a specific proposed record is independently reviewed."
        )

        notes = typer.prompt("Notes (optional, Enter to skip)", default="", show_default=False)
    except (KeyboardInterrupt, typer.Abort):
        typer.echo("\n\nStopped — progress so far is saved. Re-run `label-calls` to continue.")
        raise typer.Exit(code=0) from None

    label = HumanLabel(
        call_id=call_id,
        reviewer=reviewer,
        assessability=assessability,
        input_sha256=source_hash(RAW_DIR / f"{call_id}.json"),
        evidence=evidence,
        outcome=CallOutcomeType(outcome) if outcome != "uncertain" else None,
        promise_captured=promise_captured,
        promise_complete=promise_complete,
        dispute_existed=dispute_existed,
        compliance_violated=compliance_violated,
        operational_followup=operational_followup,
        followup_reason=followup_reason,
        expected_records=expected_records,
        notes=notes,
        labeled_at=datetime.now(UTC),
    )
    save_label(label)
    typer.echo(f"Saved label for {call_id}.\n")


def _prompt_choice(prompt_text: str, values: list[str]) -> str:
    while True:
        raw = typer.prompt(f"{prompt_text} [number or value]").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(values):
            return values[int(raw) - 1]
        if raw in values:
            return raw
        typer.echo(
            f"Not a valid choice: {raw!r}. Enter a number 1-{len(values)} or one of: {', '.join(values)}"
        )


@app.command("run-eval")
def run_eval_cmd(
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-run the pipeline even if a cached result exists."
    ),
) -> None:
    """Step 7 (eval): runs the real pipeline against every labeled call and reports agreement
    with your own human judgement — per-specialist and on the final write decision, with every
    disagreement printed in full (transcript included). Pipeline results are cached under
    fixtures/eval/pipeline_runs/ so re-running this report doesn't re-spend OpenAI credit;
    pass --refresh to force a fresh run.

    This reports agreement with one human labeller on a small, mostly-synthetic fixture set —
    see docs/eval.md for exactly what that does and does not mean. It is not a kept rate and
    says nothing about accuracy at production volume.
    """
    from collections_agent.eval.compare import run_comparison

    run_comparison(refresh=refresh)


@app.command("sim-eval")
def sim_eval_cmd(
    scenario: list[str] = typer.Option(  # noqa: B008
        None, "--scenario", "-s", help="Run only these scenario ids (repeatable). Default: all."
    ),
    agent_model: str = typer.Option("gpt-4o-mini", help="Model playing the agent."),
    customer_model: str = typer.Option("gpt-4o-mini", help="Model playing the customer."),
    max_usd: float = typer.Option(1.00, help="Hard spending cap for this run, in US dollars."),
    max_turns: int = typer.Option(14, help="Maximum agent turns per simulated call."),
    postcall: bool = typer.Option(True, "--postcall/--no-postcall", help="Also run the post-call pipeline."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estimate cost only; send nothing to OpenAI."),
    temperature: float = typer.Option(0.0, help="Sampling temperature for agent and customer."),
) -> None:
    """Simulates calls as text, with no Vapi spend: the real agent prompt and tools against a
    model playing the customer from scripted scenarios (config/sim_scenarios.yaml), then code
    checks of what the agent actually did — right tool with the right details, nothing it
    shouldn't have called, no action claimed without a tool call, no account facts read as
    digits. Optionally runs the post-call pipeline on each simulated call too.

    Spending is capped (--max-usd, default $1.00) using each request's real token usage. Run
    with --dry-run first to see the estimate. Says nothing about speech-to-text, text-to-speech,
    interruptions or latency — see docs/eval.md.
    """
    from collections_agent.eval.sim_report import run_sim_eval

    run_sim_eval(
        only=scenario,
        agent_model=agent_model,
        customer_model=customer_model,
        max_usd=max_usd,
        max_turns=max_turns,
        postcall=postcall,
        dry_run=dry_run,
        temperature=temperature,
    )


@app.command("write-back")
def write_back_cmd(call_id: str) -> None:
    """Step 5: write a completed post-call analysis to Google Sheets. Reads
    fixtures/postcall/<call_id>.json (from `run-postcall`) and
    fixtures/transcripts/<call_id>.json (from `pull-transcripts`). Costs nothing to re-run — no
    OpenAI or Vapi calls — and is safe to re-run: every write is keyed so it upserts rather than
    duplicates (see postcall/writeback.py).
    """
    from collections_agent.models.domain import PostCallAnalysis
    from collections_agent.postcall.transcript import PARSED_DIR, Transcript
    from collections_agent.postcall.writeback import write_back
    from collections_agent.sheets.client import build_sheets_backend

    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_service_account_json:
        typer.echo(
            "Set GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON in .env first "
            "(share the sheet with the service account's client_email as Editor).",
            err=True,
        )
        raise typer.Exit(code=1)

    analysis_path = Path("fixtures/postcall") / f"{call_id}.json"
    if not analysis_path.exists():
        typer.echo(
            f"{analysis_path} not found — run `run-postcall {call_id} --account-id <id>` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    analysis = PostCallAnalysis.model_validate_json(analysis_path.read_text(encoding="utf-8"))

    transcript_path = PARSED_DIR / f"{call_id}.json"
    if not transcript_path.exists():
        typer.echo(f"{transcript_path} not found — run `pull-transcripts {call_id}` first.", err=True)
        raise typer.Exit(code=1)
    transcript = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))

    if transcript.started_at is None:
        typer.echo(
            f"{transcript_path} has no started_at (parsed before that field existed) — "
            f"re-run `pull-transcripts {call_id}` to backfill it, then try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    backend = build_sheets_backend(settings.google_sheet_id, settings.google_service_account_json)
    write_back(backend, analysis, transcript)

    typer.echo(f"Wrote Call_Log row for {call_id}.")
    if analysis.write_decision.value == "exception_queue":
        typer.echo(f"Routed to Exceptions ({analysis.exception_reason}) — no PTP/Dispute rows written.")
    else:
        if analysis.promise.has_promise and not analysis.promise.downgraded_to_soft_commitment:
            typer.echo(f"Wrote PTP_Register row PTP-{call_id}.")
        elif analysis.promise.has_promise:
            typer.echo(f"Wrote Soft_Commitments row SC-{call_id}.")
        if analysis.dispute.has_dispute:
            typer.echo(f"Wrote Disputes row DSP-{call_id}.")


@app.command("followthrough")
def followthrough_cmd(
    as_of: str | None = typer.Option(
        None, "--as-of", help="YYYY-MM-DD override for testing (default: today)."
    ),
) -> None:
    """Step 6: check every still-open promise past its grace period against the Payments tab,
    resolve it to kept/partial/broken, update account reliability, and roll the day up into a
    Metrics row (see docs/metrics.md for the exact definitions). Costs nothing to re-run — no
    OpenAI or Vapi calls — and only ever touches promises still `open`, so a same-day re-run
    is a true no-op.
    """
    from datetime import date as date_cls

    from collections_agent.followthrough.job import run_followthrough
    from collections_agent.followthrough.payments import SheetPaymentsSource
    from collections_agent.metrics.rollup import compute_metrics
    from collections_agent.sheets.client import build_sheets_backend
    from collections_agent.sheets.readers import read_call_log, read_disputes, read_exceptions, read_ptps
    from collections_agent.sheets.writers import write_metrics

    settings = get_settings()
    if not settings.google_sheet_id or not settings.google_service_account_json:
        typer.echo(
            "Set GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON in .env first "
            "(share the sheet with the service account's client_email as Editor).",
            err=True,
        )
        raise typer.Exit(code=1)

    target_date = date_cls.fromisoformat(as_of) if as_of else date_cls.today()

    backend = build_sheets_backend(settings.google_sheet_id, settings.google_service_account_json)
    result = run_followthrough(backend, SheetPaymentsSource(backend), as_of=target_date)
    for warning in result.attribution_warnings:
        typer.echo(f"Attribution warning: {warning}")

    typer.echo(f"Checked promises as of {target_date.isoformat()}.")
    if result.resolved:
        by_status: dict[str, int] = {}
        for ptp in result.resolved:
            by_status[ptp.status.value] = by_status.get(ptp.status.value, 0) + 1
        typer.echo(f"Resolved {len(result.resolved)} promise(s): {by_status}")
        typer.echo(f"Updated reliability_score for account(s): {', '.join(result.accounts_updated)}")
    else:
        typer.echo("No promises were due for resolution.")

    metrics_row = compute_metrics(
        call_log=read_call_log(backend),
        ptps=read_ptps(backend),
        disputes=read_disputes(backend),
        exceptions=read_exceptions(backend),
        target_date=target_date,
    )
    write_metrics(backend, [metrics_row])
    kept_rate = metrics_row.promise_to_pay_kept_rate
    kept_value_rate = metrics_row.promise_to_pay_kept_rate_by_value
    typer.echo(
        f"\nMetrics for {target_date.isoformat()}: "
        f"kept_rate={kept_rate if kept_rate is not None else 'N/A'} "
        f"kept_rate_by_value={kept_value_rate if kept_value_rate is not None else 'N/A'} "
        f"calls_made={metrics_row.calls_made}"
    )


@app.command("test-webhook")
def test_webhook(
    host: str = "localhost",
    port: int | None = None,
    url: str | None = typer.Option(
        None,
        "--url",
        help="Full base URL to test against (e.g. an https:// tunnel URL), overriding host/port.",
    ),
) -> None:
    """Dry-run the local webhook server against realistic fake payloads for all 7 tools plus
    an end-of-call-report, printing what each handler did. Also exercises the X-Vapi-Secret
    auth path (including a deliberately wrong secret) so a 401 shows up here instead of as
    dead air on a live call. Run `serve-webhook` in another terminal first.
    """
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta

    import httpx

    from collections_agent.webhooks.auth import VAPI_SECRET_HEADER
    from collections_agent.webhooks.sample_payloads import SAMPLE_END_OF_CALL_REPORT, SAMPLE_TOOL_CALLS

    settings = get_settings()
    if url:
        base_url = url.rstrip("/")
    else:
        port = port or settings.webhook_port
        base_url = f"http://{host}:{port}"

    if not settings.vapi_server_secret:
        typer.echo("Set VAPI_SERVER_SECRET in .env first (any value — this is a local dry run).", err=True)
        raise typer.Exit(code=1)

    ok_headers = {VAPI_SECRET_HEADER: settings.vapi_server_secret}
    bad_headers = {VAPI_SECRET_HEADER: "deliberately-wrong-secret"}

    typer.echo("== Auth check ==")
    try:
        resp = httpx.post(
            f"{base_url}/vapi/tool-calls",
            json={"message": {"toolCallList": [SAMPLE_TOOL_CALLS[0]]}},
            headers=bad_headers,
            timeout=10,
        )
    except httpx.ConnectError as e:
        typer.echo(f"Could not reach {base_url} — is `serve-webhook` running?", err=True)
        raise typer.Exit(code=1) from e

    if resp.status_code == 401:
        typer.echo(f"[PASS] wrong secret correctly rejected -> {resp.status_code}")
    else:
        typer.echo(f"[FAIL] wrong secret was NOT rejected -> {resp.status_code}: {resp.text}", err=True)
        raise typer.Exit(code=1)

    typer.echo("\n== Tool-call dry run (correct secret) ==")
    all_ok = True
    for sample in SAMPLE_TOOL_CALLS:
        call = deepcopy(sample)
        if call["name"] == "record_ptp":
            call["arguments"]["date"] = str(datetime.now(UTC).date() + timedelta(days=7))
        resp = httpx.post(
            f"{base_url}/diagnostics/tool-calls",
            json={"message": {"type": "tool-calls", "toolCallList": [call]}},
            headers=ok_headers,
            timeout=10,
        )
        reply = resp.json()["results"][0] if resp.status_code == 200 else {"error": resp.text}
        result = reply.get("result", reply.get("error", "missing tool response"))
        is_error = resp.status_code != 200 or "error" in reply
        all_ok = all_ok and not is_error
        status = "FAIL" if is_error else "PASS"
        typer.echo(f"[{status}] {call['name']}({call['arguments']}) -> {result}")

    typer.echo("\n== End-of-call-report dry run ==")
    resp = httpx.post(
        f"{base_url}/vapi/events", json=SAMPLE_END_OF_CALL_REPORT, headers=ok_headers, timeout=10
    )
    typer.echo(f"[{'PASS' if resp.status_code == 200 else 'FAIL'}] status {resp.status_code}: {resp.json()}")

    if not all_ok:
        raise typer.Exit(code=1)
    typer.echo("\nAll checks passed.")


@app.command("latency-report")
def latency_report_cmd(
    raw_dir: Path = typer.Option(  # noqa: B008
        Path("fixtures/raw"), help="Directory of saved provider call payloads."
    ),
    out: Path | None = typer.Option(None, "--out", help="Save full timing evidence as JSON."),  # noqa: B008
) -> None:
    """Report measured historical turn timings; no network requests or model calls."""
    from collections_agent.eval.latency import latency_report

    result = latency_report(raw_dir)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    typer.echo(
        f"{result['calls_inspected']} saved calls inspected; "
        f"{result['calls_with_turn_measurements']} with turn measurements; "
        f"{result['calls_without_turn_measurements']} unavailable."
    )
    for name, metric in result["metrics"].items():
        typer.echo(
            f"{name}: n={metric['samples']}, p50={metric['p50_ms']} ms, "
            f"p95={metric['p95_ms']} ms, max={metric['max_ms']} ms"
        )
    typer.echo("Historical provider measurements; no production or model-ranking claim. No API calls made.")
    if out:
        typer.echo(f"Full evidence saved to {out}")


@app.command("register-call")
def register_call_cmd(call_id: str, account_id: str) -> None:
    """Bind a provider call ID to an account before its first tool request (trusted operator)."""
    from collections_agent.sheets.client import load_accounts_and_invoices
    from collections_agent.webhooks.operations import register_call

    settings = get_settings()
    accounts, invoices = load_accounts_and_invoices(settings)
    account = next((a for a in accounts if a.account_id == account_id), None)
    if account is None or account.opted_out:
        raise typer.BadParameter("Account unknown or suppressed")
    register_call(
        settings.operations_db_path,
        call_id,
        account_id,
        account.contact_phone,
        [i for i in invoices if i.account_id == account_id],
    )
    typer.echo("Trusted call scope saved. Context expires after four hours.")


@app.command("operations")
def operations_cmd(
    complete: str | None = typer.Option(None, help="Receipt ID to mark complete after human action."),
    resolution: str = typer.Option("", help="Evidence of completion, required with --complete."),
    summary: bool = typer.Option(False, "--summary", help="Show queue counts, SLA and backlog age."),
) -> None:
    """Inspect durable follow-up work; completion is separate from accepting a request."""
    from collections_agent.webhooks.operations import complete_action, list_actions, operation_metrics

    path = get_settings().operations_db_path
    if complete:
        complete_action(path, complete, resolution)
    if summary:
        typer.echo(json.dumps(operation_metrics(path), indent=2))
        return
    actions = list_actions(path)
    typer.echo(json.dumps(actions, indent=2))
    typer.echo(
        f"{sum(a['status'] == 'pending' for a in actions)} pending; "
        f"{sum(a['status'] == 'completed' for a in actions)} completed. "
        "No calls or documents sent by this command."
    )


def main() -> None:
    # Windows consoles default to a codepage (e.g. cp1252) that can't encode characters like
    # "→" — reconfigure to UTF-8 so a rendered prompt or dashboard payload never crashes the
    # CLI outright; worst case a character renders oddly instead of raising.
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    app()
