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

    # No real PTP/dispute/call-log history exists yet (fixtures only cover accounts and
    # invoices) — real history flows in once calls happen and Step 5 write-back lands.
    ranked: list[tuple[float, object]] = []
    for account in accounts:
        suppression = is_suppressed(account, invoices, call_log=[], open_ptps=[], now=suppression_now)
        if suppression.suppressed:
            continue
        score = priority_score(account, invoices, ptp_history=[], call_log=[], weights=weights, as_of=as_of)
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
        top_account, invoices, ptp_history=[], open_disputes=[], call_log=[], as_of=as_of
    )
    prompt = render_system_prompt(context_pack, settings.company_name, current_date=as_of)

    typer.echo(f"Top-priority account: {top_account.account_id} ({top_account.customer_name}, score {top_score})")
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
    typer.echo(f"promise: has_promise={analysis.promise.has_promise} complete={analysis.promise.is_complete} "
               f"downgraded_to_soft_commitment={analysis.promise.downgraded_to_soft_commitment} "
               f"(confidence {analysis.promise.confidence:.2f})")
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


@app.command("test-webhook")
def test_webhook(
    host: str = "localhost",
    port: int | None = None,
    url: str | None = typer.Option(
        None, "--url", help="Full base URL to test against (e.g. an https:// tunnel URL), overriding host/port."
    ),
) -> None:
    """Dry-run the local webhook server against realistic fake payloads for all 7 tools plus
    an end-of-call-report, printing what each handler did. Also exercises the X-Vapi-Secret
    auth path (including a deliberately wrong secret) so a 401 shows up here instead of as
    dead air on a live call. Run `serve-webhook` in another terminal first.
    """
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
    for call in SAMPLE_TOOL_CALLS:
        resp = httpx.post(
            f"{base_url}/vapi/tool-calls",
            json={"message": {"type": "tool-calls", "toolCallList": [call]}},
            headers=ok_headers,
            timeout=10,
        )
        result = resp.json()["results"][0]["result"] if resp.status_code == 200 else resp.text
        is_error = resp.status_code != 200 or "error" in result
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
