# Production foundations: implementation and evidence

This records the changes made after the [production review](PRODUCTION_REVIEW_2026-10-15.md). The review is a point-in-time audit; its original findings describe the pre-change repository. This file distinguishes implemented behavior from remaining release evidence.

## Implemented locally

- Withdrew all human benchmark claims derived from the nine randomly labeled calls. Preserved the original local labels and recorded invalidation in `fixtures/eval/benchmark_status.json`. README and demo show replacement review as pending.
- Added version 2 labels with reviewer, timestamp, source hash, evidence, uncertainty and revision history. Blind annotation separates expected records from business follow-up. Write correctness stays unscored until a particular proposed record is reviewed. Changed-source labels and stale pipeline caches are rejected; ordinary reports do not silently launch new API requests.
- Required positive finite promise amounts and unique invoice resolution; accepted references become canonical IDs. Live registered-call validation enforces the same date and amount policy.
- Added persistent payment allocations, duplicate-import protection, pre-promise payment exclusion, deterministic allocation order and legacy reconciliation warnings. A payment cannot be reused by a later promise after a restart/re-run. Settled promises cannot be reopened by re-analysis.
- Revised the metric contract: explicit counts, N/A for missing data, call-cohort record review, verified resolution denominators, unresolved maturity backlog, actual attributed recovery and partial call unit cost. Legacy fulfillment results without attribution metadata are excluded from verified rates.
- Connected pre-call suppression, prioritization and context to CRM promise, dispute and call history. Persisted local opt-outs are applied when accounts are loaded.
- Added durable SQLite action receipts for registered production calls: bounded account/invoice scope, argument validation, deduplication by call/tool-call ID, immutable call binding, immediate opt-out suppression, human task ownership and completion evidence. Accepted tasks are distinct from completed callbacks/documents. Operations metrics expose accepted, pending, completed, overdue, SLA denominator and oldest pending age.
- Strengthened simulation grading: failed or unrelated tools do not support an action claim; opening speech is included in disclosure checks; end-to-end text results are separate from conditional post-call diagnostics. No scenario expectations, system prompts or tool schemas were changed to improve scores.
- Added simulation manifests with source hashes, materialized context, date, models, prices and settings. Bounded API outputs and concurrent budget reservations replace the old output-unbounded preflight; retries are disabled on simulation requests. Missing usage retains its reservation and stops the run. Harness errors save the affected scenario and stop. The application meter is not a provider billing limit; verify the configured prices before any future paid experiment. [OpenAI API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

## Operating the new action path

`OPERATIONS_DB_PATH` defaults to `fixtures/operations.db`. Production tool calls must carry a provider `message.call.id` bound through trusted application code before their first request:

```powershell
uv run collections-agent register-call PROVIDER_CALL_ID ACCOUNT_ID
uv run collections-agent operations --summary
uv run collections-agent operations
uv run collections-agent operations --complete RECEIPT_ID --resolution "Operator evidence of completed action"
```

Registration reads the account/invoices from the configured source, refuses unknown or opted-out accounts, and uses the account's actual contact phone as the suppression key. A model-supplied contact ID cannot redirect an opt-out. Context expires after four hours; an opt-out on an already registered scope remains permitted. For an automated caller, invoke the same registration function immediately after provider call creation and before tools are enabled. That automated dispatch integration remains outstanding; registration is currently an operator CLI.

`test-webhook` now uses an authenticated diagnostics endpoint with temporary synthetic state to exercise validation and receipts without putting test opt-outs or tasks into real operations. Normal production requests still require trusted registration. The public synthetic live demo retains its separate preview/stub behavior; it is not evidence of production CRM action completion.

The queue accepts requests. It does **not** place a callback, send a document, verify money received or connect a human merely by returning a receipt. Humans act through their existing workflow and record evidence of completion. Its 24-hour task target is provisional. A real worker, delivery integration, task reassignment and calibrated service targets remain future work.

## Storage and recovery boundaries

Use durable storage and one application instance for the SQLite action store; back it up with appropriate access restrictions. No deployment or storage plan has been changed. An ephemeral host loses these guarantees across redeploy/restart. A distributed deployment needs a transactional shared database and worker coordination.

Run payment follow-through as one serialized job. Its Sheets allocation writes do not make concurrent jobs safe. Use stable payment IDs, reconcile old statuses before trusting fulfillment trends, and do not overwrite prior ledger entries with unrelated transactions. Refund/reversal handling and timestamped same-day attribution remain unimplemented.

The action ledger is durable; the existing end-of-call background analysis is still an in-process task. Provider event ingestion, analysis retries/dead-letter handling and reconciliation are separate outstanding work. Raw log redaction and retention, verification before revealing account facts, dispatch kill-switch, readiness/alerts and hosted configuration verification remain necessary for a customer pilot.

## Evidence still required

1. Rebuild distinct human-reviewed cases using [rubric v2](EVAL_LABELING_RUBRIC_V2.md). No replacement labels have been invented. Similar historical calls can be debugging examples, but do not establish broad coverage.
2. Run one approved text experiment under the existing caps, after checking local prices. Historical runs have not been changed or silently rescored. New grader results must carry the version difference.
3. Execute controlled audio cases: stop requests and interruption recovery; noise and accents; dates, amounts and invoice identifiers; silence/voicemail; slow/rejected tools; disconnections; handoff and callback follow-through. Measure per-turn latency and entity accuracy against audio truth.
4. Connect a controlled provider call to the registered action path, demonstrate restart/duplicate delivery and operator closure, then test an analysis failure/recovery path on durable infrastructure.
5. Establish a bounded pilot with operational owner, permissions and jurisdiction review, release gates, rollback and a defined eligible-account outcome cohort.

These changes improve the prototype's foundations. They do not establish real customer recovery, production accuracy, a validated voice SLA or a production-ready deployment.

## Validation performed

- One full offline run: `uv run pytest -q` — **373 passed**, one Starlette/AnyIO deprecation warning. The webhook diagnostic test passes too. No full-suite rerun was performed.
- Ruff checks on the edited Python modules and regression tests passed. `git diff --check` passed.
- `sim-eval --dry-run --max-usd 0.50`: 21 scenarios, gpt-4o-mini, post-call enabled, estimated **$0.23**.
- `sim-eval --dry-run --agent-model gpt-4o --max-usd 1.00`: 21 scenarios, post-call enabled, estimated **$2.57**. The estimate exceeds that cap; no paid run was launched.
- `run-eval` exits without scoring or API requests because no reviewed v2 labels exist. This is the intended invalid-benchmark guard.
- No paid simulation, live call, deployment or commit was performed. Historical simulation artifacts and original labels were preserved; `.env` was not edited.

The API output limit and retry controls follow the [official OpenAI documentation](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) and installed SDK signature. Budget controls are covered with fake clients and concurrent reservation tests; they have not been validated by spending additional credit.

## Portfolio refinement and timing evidence

The owner requested a polished portfolio release by tomorrow, retaining the current visual identity. The demo, How it is built, Evals and What I learned write-ups have been refined together:

- Added a three-step demo journey and an Evals timing panel backed by an aggregate endpoint restricted to the two fixed saved examples. Public output excludes visitor call data, call identifiers and account details.
- Added `latency-report`, which runs locally without a model or network requests. The current saved set has nine calls, eight with usable turn measurements: **65 turns**, median **1,376 ms**, nearest-rank p95 **2,478 ms**, maximum **2,903 ms**. One call has no usable measurements. These are historical provider observations, not production performance or an independently verified audible timing interval.
- The public two-example subset contains **14 turns**, median **1,493.5 ms**, p95 **2,579 ms**. The UI identifies its population separately from the full local report. No model attribution is inferred from incomplete old metadata.
- Removed the historical model toggle that mixed one model's reference voice cost with another call's analysis cost. Display costs in source USD, with the 1,000-call illustration explicitly excluding hosting and human operations.
- Replaced blanket action validation badges with acknowledged/rejected/unknown tool states. A completed analysis does not establish that a callback occurred, a document was delivered or CRM rows were saved.
- Corrected write-up claims about suppression logging, identity verification, public write previews and the status of model-based evaluation. Added measurement-quality and payment-attribution lessons. Model-reported confidence is explicitly uncalibrated.
- Added bounded replay analysis failure handling. Specialist failures produce an explicit incomplete-analysis event and no final write decision; UI keeps the available conversation and marks costs/results unavailable. Escaped model-provided fact text when rendering it into HTML.

Verification: **34 targeted offline tests passed** across timing evidence, demo sessions, caps and webhook diagnostics; edited-module Ruff checks and Node inline-JavaScript syntax checks passed. HTML checks found no mismatched/unclosed tags or duplicate IDs. The local aggregate endpoint returned the expected two-example data. No further full-suite run, paid call, deployment or commit was performed.

A visual browser check could not be completed because the available UI tool reported no connected browsers. The local preview can be reviewed in a user browser; responsive appearance and interaction still need that visual check. A calibrated independent conversation judge, new human labels, controlled audio tests, durable post-call jobs and hosted recovery tests remain open.

## Portfolio publication checks

The owner authorized publishing the corrected portfolio release. A public-browser review of the previous hosted version found working desktop/mobile layouts but stale invalid benchmark numbers, no timing evidence, low-contrast supporting text and missing evidence links. The release adds section navigation and shareable tab/section links, improves contrast, places the project-funded API usage explanation beside the controls, and links the case study, failure log, metric contract and review method.

Pre-publication validation: **152 targeted offline tests passed**, including demo, timing, webhook, production foundations, metrics, payment follow-through, write-back and simulation regression checks. Ruff passed across source and tests; HTML nesting, unique IDs and inline JavaScript syntax passed. A credential-pattern scan of the changed release files found no matches. This was not another full-suite or paid simulation run. Local labels, `.env`, generated outputs and simulation-run artifacts are excluded from the release commit.

Render's existing free service deploys automatically from `master`. Hosted verification is performed after the authorized push; publishing this portfolio release does not establish customer-pilot readiness or durable storage on that plan.
