# Metric contract, version 2

Implemented 15 October 2026. Every rate needs its population, window, numerator and denominator. Empty populations and unavailable instrumentation are N/A (`null` in JSON, blank in Sheets). They are not measured zeroes. Historical human-label scores are invalidated; see [evaluation methodology](eval.md).

## What the current rollup measures

The daily call cohort consists of persisted `Call_Log` rows whose `called_at` UTC date matches the requested date. This is logged calls, not all provider attempts: unanswered attempts or failed post-call processing can be absent. It cannot establish production reach or availability until provider-event ingestion is complete.

| Field | Definition | Supporting counts / limitations |
|---|---|---|
| `calls_made` | Logged calls in the UTC day | Not an authoritative dial-attempt count |
| `calls_suppressed` | Unavailable until dispatch decisions are logged | `null`; enforced opt-outs do not establish a count of avoided calls |
| `contact_rate` | Calls classified other than `no_answer` / logged calls | `contacted_calls / calls_made`; includes hostile and wrong-party answers |
| `structured_outcome_rate` | Calls other than `no_answer`, `hostile`, `wrong_person` / logged calls | `business_outcome_calls / calls_made`; not proof of completed work |
| `human_review_rate` | Distinct cohort call IDs with a record-review exception / cohort calls | `record_review_calls / calls_made`; includes later reviews; deduplicated |
| `disputes_surfaced` | Disputes opened on the UTC day | Records, not resolved cases |
| `call_cost_usd` | Sum of reported provider call costs in the cohort | N/A if any cost is missing; excludes analysis and human labor |
| `cost_per_contact_usd` | Reported cohort call cost / inferred contacts | Includes failed cohort calls' reported costs; partial unit economics only |

The record-review rate is a retrospective cohort metric. A late exception can revise an older date. It does not mean that fraction of customers required human business follow-up, and it is not extraction accuracy. A correctly saved dispute can still require a billing investigation.

## Promise fulfillment and attributed cash

A firm promise requires a positive finite amount, a date strictly after the call date, a supported payment method, and uniquely resolved invoice references. Every reference must resolve; an invented reference alongside a valid one is rejected. Accepted references become canonical invoice IDs. Amount must be within the selected invoices' outstanding balances. Extraction stays separate from these deterministic checks.

Promises mature three calendar days after their promised date. This grace period is a product assumption, not a measured settlement SLA. Evaluation waits for maturity; payments after that deadline do not change on-time fulfillment. Late collections need a separate recovery window.

The cumulative snapshot includes promises captured and matured by the requested date, resolved by that date, and carrying the new `resolved_on` attribution marker. Superseded and open promises are excluded from resolved rates. Legacy resolutions without the marker are counted separately as unverified.

| Field | Definition |
|---|---|
| `resolved_promises` | Verified matured promises evaluated as kept, partial or broken |
| `kept_promises` | Verified matured promises paid in full, within 0.01 rounding tolerance |
| `promise_to_pay_kept_rate` | `kept_promises / resolved_promises` |
| `resolved_promised_value` | Amount promised by verified resolved promises |
| `promise_to_pay_kept_rate_by_value` | Promised value of fully kept promises / resolved promised value; value-weighted full fulfillment, not cash recovery |
| `attributed_payment_value` | Sum of persisted payment allocations, capped per promise |
| `attributed_recovery_rate` | Attributed payment value / resolved promised value; includes partial payments |
| `matured_unresolved_promises` | Matured promises still open; display beside rates so stalled processing remains visible |
| `legacy_unverified_promises` | Matured legacy resolutions without new attribution metadata; require reconciliation |

Example: two promises of 100 each, with 100 and 50 allocated respectively, give a 50% kept rate, a 50% value-weighted full-fulfillment rate, and 75% attributed recovery. These are synthetic calculations; no real recovery uplift has been measured.

## Payment attribution policy

The ledger remains manually entered bank-reconciled Payments data. An already-paid claim is a pending reconciliation task and never becomes a payment by itself.

- Prefer an immutable bank/importer `payment_id`, scoped by invoice. Otherwise use invoice plus reference; for legacy entries without either use invoice/date/method/amount. Identical legacy rows count once. IDs distinguish legitimate identical transactions.
- Conflicting rows sharing an identity fail visibly. Positive finite amounts are required. Refunds/reversals need an explicit reconciliation model before ingestion.
- Only payments after the capture date and on or before the grace deadline qualify. Date-only payments on the capture day are excluded because their ordering is unknown. A timestamped feed can remove that conservative limitation later.
- Allocate mature promises by promised date, capture time and stable promise ID. Consume each payment's available amount once. Persist allocations and resolution date so later runs cannot reuse money.
- Legacy kept/partial promises reserve potentially consumed money. Overlapping new promises remain open with a warning until reconciled. Ledger edits that invalidate allocations stop processing.
- Re-analysis cannot reopen a settled promise or erase its allocation. Corrections require explicit reconciliation.

The job assumes one serialized worker. Sheets upserts do not provide distributed transactions; concurrent follow-through workers are unsupported. Policy allocations do not prove that the voice agent caused payment.

## Metrics still requiring evidence

`uv run collections-agent latency-report --out outputs/eval/latency-evidence.json` now extracts historical provider timing evidence from saved calls. It reports measured-turn median, nearest-rank p95, mean, maximum, component sample counts and unavailable calls. The public Evals panel exposes only the two fixed demo examples, with no visitor transcripts or call identifiers. These measurements do not establish independently observed listener latency, model superiority or a production SLA. Tool/no-tool timing association, interruption recovery and time to a substantive answer remain unassessed.

| Metric | Required evidence / denominator |
|---|---|
| Right-party contact | Verified intended contacts / answered calls, from provider events and verification evidence |
| Safe-write precision | Independently adjudicated correct, permitted proposed records / reviewed proposed records |
| Workflow completion | Calls whose required actions reached the defined durable state / eligible assessed calls, split by intent |
| Callback fulfillment | Completed callbacks within SLA / due callback tasks; acceptance is separate |
| Speech response latency | End of customer speech to first audible response, per-turn p50/p95 and sample size |
| Interruption recovery | Correct stop/yield and task recovery / tested interruptions |
| Entity accuracy | Correct critical amounts, dates, invoice IDs / reviewed audio opportunities |
| Complete unit cost | Telephony, STT, LLM, TTS, platform, analysis, retries and human review / verified outcomes |
| Incremental recovery | Fixed-window eligible-account recovery compared with a justified control cohort |

These remain unmeasured. Do not replace them with model confidence, synthetic pass rates, one-call latency means, or guessed business savings.
