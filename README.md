# Collections Voice Agent

A B2B collections voice agent: context-assembly and commitment-capture system that happens
to use voice. Full architecture in `COLLECTIONS_AGENT_DESIGN.md` (design source) and the
execution plan this build follows.

**Thesis:** collection calls fail from missing context and lost commitments, not from bad
talking. **Headline metric:** promise-to-pay kept rate.

## Product decisions worth calling out

- **90+ days overdue is never automated.** Those calls carry legal/relationship risk; they're
  routed to a human. This is a product decision, not a technical limitation.
- **Disclosure:** the agent states it's automated in its opening line. Costs a little rapport,
  buys legitimacy under most emerging AI-disclosure norms.
- **Confidence-gated write-back:** low-confidence or disagreeing post-call analysis goes to a
  human exception queue instead of auto-writing to the CRM.
- **Follow-up emails are drafted, never sent automatically.**

See `docs/guardrails_and_escalation.md` for the full guardrail policy once written.

## Stack

Python 3.11, managed with `uv`. Google Sheets as the CRM (single source of truth, no local
DB). Vapi for the voice layer. OpenAI (gpt-4o-mini) for the voice agent's backend model and
the post-call specialist pipeline — chosen over Anthropic purely on available credit
(no Anthropic credit; ~$8 OpenAI, ~$7 Vapi). The in-call model is wired as bring-your-own-key
so it bills your OpenAI balance, not Vapi-hosted credits — see `.env.example`.

## Setup

```powershell
uv sync
cp .env.example .env   # then fill in credentials
```

Required before Step 0:
1. A Google Cloud service account with the Sheets API enabled.
2. A Google Sheet, shared with that service account's `client_email` as Editor.
3. `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` set in `.env`.

## CLI

```powershell
uv run collections-agent gen-data          # Step 0: generate ~25 accounts / ~60 invoices locally
uv run collections-agent seed-sheet        # Step 0: create CRM tabs + seed the Google Sheet
uv run collections-agent serve-webhook     # Step 2: run the local tool-call server (tunnel it before use)
uv run collections-agent test-webhook      # Step 2: dry-run all 7 tools + auth against the running server
uv run collections-agent create-assistant  # Step 2: create the Vapi assistant, wired to your tunnel + BYOK
uv run collections-agent test-call         # Step 2: render + optionally dial a test call with real context
uv run collections-agent pull-transcripts  # Step 3: fetch a call, save raw + fixed parse
uv run collections-agent run-postcall      # Step 4: 4 specialists + supervisor against a pulled transcript
uv run collections-agent write-back        # Step 5: write a post-call analysis back to the Sheet
```

More commands (`build-queue`, `followthrough`, `eval`, `metrics`) land as later build steps
are implemented — see the execution plan for the full Step 0–7 sequence.

## Tests

```powershell
uv run pytest
```

Everything except the one-off live Sheets/Vapi/OpenAI calls runs offline, with no
credentials required — see each module's tests for the pure-function/fake-backend pattern.

## Fixture discipline

After Step 3 (pulling recorded Vapi transcripts into `fixtures/`), all further iteration
replays those fixtures. Vapi retains call recordings for only 14 days — pull transcripts
promptly after recording test calls.
