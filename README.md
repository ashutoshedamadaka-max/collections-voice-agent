# Collections Voice Agent

A B2B collections voice agent: context-assembly and commitment-capture system that happens
to use voice. Full architecture in `COLLECTIONS_AGENT_DESIGN.md` (design source) and the
execution plan this build follows.

**Thesis:** collection calls fail from missing context and lost commitments, not from bad
talking. **Headline metric:** promise-to-pay kept rate.

## Product decisions worth calling out

- **90+ days overdue is never automated.** Those calls carry legal/relationship risk; they're
  routed to a human. This is a product decision, not a technical limitation.
- **Disclosure and authority, merged into one fixed opening line.** The agent states it's
  automated and asks whether it's speaking with the right person in a single deterministic
  `firstMessage`, spoken by Vapi before the model runs at all — not two separate turns with the
  model generating its own authority question later. These are the two things in this whole
  design that must never vary: never reworded, never skipped, and (for authority) never asked
  twice. Putting both in the fixed opening makes that structurally guaranteed instead of
  dependent on the model remembering to say them — a small architectural improvement, not just
  a pacing fix. Costs a little rapport for the disclosure; buys legitimacy under most emerging
  AI-disclosure norms.
- **Confidence-gated write-back:** low-confidence or disagreeing post-call analysis goes to a
  human exception queue instead of auto-writing to the CRM.
- **Follow-up emails are drafted, never sent automatically.**

See `docs/guardrails_and_escalation.md` for the full guardrail policy once written.

## Language support — English and Hindi only; a pre-call, not mid-call, property

`account.preferred_language` (`en` / `hi`) drives the voice provider's language, the
transcriber's language, the fixed opening line, and the rest of the system prompt — set once
per account **before** the call starts. Two deliberate scope limits, both decisions rather than
oversights:

- **Hinglish was tried and dropped.** Two separately-worded prompt rewrites both failed the
  same way — the model settled into formal Devanagari regardless of the instruction — on both
  gpt-4o-mini and gpt-4o. It was also the least-tested path through this pipeline, and
  validating a third rewrite would cost real Vapi credits with no evidence it would fare any
  better. English and Hindi only. See `docs/FAILURES.md`, 2026-09-29.
- **The agent does not adapt to the language the customer actually speaks, mid-call.** Language
  is decided once, per account, before the call starts — never negotiated during it (asking
  wastes the opening seconds and reads as an IVR menu). If a customer on an English-configured
  account speaks Hindi, the agent stays in English; it does not detect and switch. This is
  designed, not an oversight: mid-call switching would double the conversation paths to design
  and test, and would hand the post-call specialists a mixed-language transcript — exactly the
  untested territory Hinglish was just dropped to avoid. The backlog item below is the intended
  eventual fix, and it is designed but **unvalidated** — nothing currently detects or writes
  back a customer's actual spoken language.

That wiring only covers the call itself. The four post-call specialists, the reason-code
taxonomy, and the regex-based arithmetic-consistency extraction were all built and tested
against English transcripts only — a Hindi call produces a Hindi transcript, and that path has
never been run. **English is the default for evaluation and the demo; Hindi is a demonstrated
capability on the voice layer, not a validated pipeline end to end.** See `docs/FAILURES.md`
for the full caveat. Backlog, not built: detecting the customer's actual spoken language
mid-call and writing it back to the account so the *next* call opens in the right language.

## Stack

Python 3.11, managed with `uv`. Google Sheets as the CRM (single source of truth, no local
DB). Vapi for the voice layer. OpenAI for the voice agent's backend model (`gpt-4o`, currently
— see `docs/FAILURES.md` for why this moved off `gpt-4o-mini`) and the post-call specialist
pipeline (`gpt-4o-mini`, unaffected by that change) — chosen over Anthropic purely on available
credit (no Anthropic credit; ~$8 OpenAI, ~$7 Vapi). The in-call model is wired as
bring-your-own-key so it bills your OpenAI balance, not Vapi-hosted credits — see `.env.example`.

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
uv run collections-agent followthrough     # Step 6: resolve due promises against Payments, roll up Metrics
uv run collections-agent label-calls       # Step 7: record your own judgement of a saved call (resumable)
uv run collections-agent run-eval          # Step 7: compare the real pipeline against your labels
```

More commands (`build-queue`) land as later build steps are implemented — see the execution
plan for the full Step 0–7 sequence. `docs/metrics.md` has the exact definition of every
Metrics tab column, including why the promise-to-pay kept rate is reported both as a count and
rupee-weighted. `docs/eval.md` covers the specialist-accuracy eval — see "Eval" below.

## Tests

```powershell
uv run pytest
```

Everything except the one-off live Sheets/Vapi/OpenAI calls runs offline, with no
credentials required — see each module's tests for the pure-function/fake-backend pattern.

## Eval

**n = 9.** The only specialist-accuracy eval in this repo compares the real pipeline's output
against one person's judgement on the nine saved calls in `fixtures/raw/` — most of them
synthetic test calls, not real customer calls. It reports agreement with a human labeller on
nine calls and nothing else: not a kept rate, not an accuracy estimate, not anything that
should be read as implying production volume or real-world performance. See `docs/eval.md` for
exactly what it measures, how account context is resolved for the four calls whose accounts no
longer exist in the current dataset, and the full methodology.

```powershell
uv run collections-agent label-calls   # one call at a time, resumable, saved to fixtures/eval/labels.json
uv run collections-agent run-eval      # runs the real pipeline, reports agreement, prints every disagreement in full
```

`fixtures/eval/labels.json` (your labels) is committed — it's hard-won human judgement, not
something to regenerate. `fixtures/eval/pipeline_runs/` (the pipeline's cached output per call)
is not — it's regenerable any time with `run-eval --refresh` and costs real OpenAI credit to
produce, same budget note as `run-postcall`.

## Fixture discipline

After Step 3 (pulling recorded Vapi transcripts into `fixtures/`), all further iteration
replays those fixtures. Vapi retains call recordings for only 14 days — pull transcripts
promptly after recording test calls.

This is also why the demo console's replay mode (`docs/DEMO_UI_SPEC.md`) pushes a saved fixture
through the *real* pipeline instead of hand-written example data: it's what caught the
invoice-ID hyphen bug (`docs/FAILURES.md`, 2026-09-30). A hardcoded fixture, written to already
match, could never have exposed a real string-matching failure — only real data forced through
real code did.
