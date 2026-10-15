# Roadmap: controlled pilot readiness, then design revamp

Prepared 15 October 2026. This is a proposed delivery sequence, not completed implementation or permission for paid calls, hosting changes or deployment. It builds on [implemented foundations](IMPLEMENTATION_STATUS_2026-10-15.md).

Schedule update: the owner wants a polished portfolio version by tomorrow and chose refinement of the existing visual identity. Prioritize measured evidence, consistent demo/error states and accurate write-ups across How it is built, Evals and What I learned. The complete customer-pilot readiness backlog remains separate; the short deadline does not establish deployment reliability, valid human benchmark results or calibrated judge performance.

## Target and scope

Build a reliable, inspectable English-language B2B collections workflow for a small, human-supervised pilot. Preserve the managed Vapi voice stack. Use synthetic accounts in development and the public portfolio demonstration. Humans own document delivery, callbacks, dispute resolution and bank reconciliation until an explicit integration is delivered and tested.

The portfolio should demonstrate product decisions and evidence: a complete successful journey, a handled failure, accurate metrics, a valid evaluation process, operational ownership and clear limits. Demonstrated staging readiness does not establish live customer adoption, collection uplift or sustained production availability.

Retain the current protections: no higher simulation caps, no full paid reruns without approval, no edits to scenario expectations to make failures disappear, and no secrets or runtime call artifacts committed. Intentional prompt/policy improvements need a new version and a preserved baseline.

## Delivery sequence

| Milestone | Work | Evidence required to finish |
|---|---|---|
| 0. Freeze the baseline and release contract | Record the current source/config version and known failures. Define permitted automation, identity policy, supported language, operator responsibilities and metric denominators. Review existing pending changes without overwriting local artifacts. | One agreed release checklist and versioned baseline; no new accuracy claims. |
| 1. Measure and establish trustworthy reference cases | Extract existing Vapi turn metrics. Prepare the first four reviewed cases, then expand toward the twelve distinct cards. Freeze facts before showing model judgments. Add an eval judge interface and offline fixtures. | Latency report from existing calls; source-linked labels; judge outputs include evidence and uncertainty; no synthetic predictions presented as human labels. |
| 2. Complete and harden the workflow | Automatically bind trusted account scope to provider calls; persist end-of-call events before acknowledgement; process durable jobs with bounded retries; reconcile actions with post-call records; enforce opt-outs. | One controlled call produces the correct persistent records and a resolvable human task. Duplicate, restart and failed-processing tests show no lost jobs or duplicate effects. |
| 3. Validate voice behaviour and judge reliability | Conduct approved calls in batches; inspect audio, tool traces and records; compare judge decisions to independent human review. Test interruption, correction, silence, poor recognition and failure communication. | Versioned quality/latency/cost report, explicit coverage and unknowns, observed failures preserved, critical failures resolved/retested. |
| 4. Pass staging and operations gates | Verify durable hosting, storage restoration, service readiness, operator access, monitoring, kill-switch and rollback. Establish ownership and review procedures. | Successful staging drill: call, saved record, queued work, restart recovery, operator closure, reconciled payment and updated metrics. Hosting configuration is verified, not inferred from repo files. |
| 5. Redesign the experience | Redesign the portfolio journey and operator workspace using stable data contracts and demonstrated workflows. Implement responsive and accessible loading/error/empty/pending/completed states. | A first-time visitor can understand and inspect the evidence; an operator can find, review and resolve work without confusing acceptance with completion. |
| 6. Final regression and release | Verify new UI behaviour, permissions, caps and unchanged workflow semantics. Review the concrete deployment and costs, then publish when authorized. | Release report, checked disclosure and evidence, rollback instructions and approved deployment. A real-customer pilot remains a separate controlled launch. |

Milestone 1 can progress alongside local work in milestone 2. Paid baseline audio calls should wait until the tested path can preserve their evidence. The design revamp starts after the core workflow/data contracts and technical gates are stable; it need not wait for a large real-customer dataset.

## Milestone 1: latency and eval details

### Latency

Import `artifact.performanceMetrics.turnLatencies` from saved provider payloads, with call/model/config provenance where available. Report sample counts and missing data. Separate provider metrics from independently observed playback timings. Do not invent model attribution when old payload metadata is incomplete.

Show median/p50, p95 and maximum turn latency, alongside relevant component timings. Keep tool and non-tool turns separate when the source can associate them reliably; otherwise flag the missing association. Define the percentile method and show the small sample size. Missing turns/calls are N/A, including zero-turn calls with a provider average of zero.

For new controlled calls, capture a correlated timing trace: end of customer speech, first agent audio, tool start/end and first substantive answer. Do not count a quick filler as proof that the requested answer arrived quickly. Measure interruption stop/recovery separately. Do not add overlapping streaming durations to fabricate end-to-end latency.

Vapi supports per-turn latency limits in voice simulations; chat mode lacks the audio measurements. We must select thresholds for this use case from evidence rather than treating a provider default as our product SLA. [Vapi latency definitions](https://docs.vapi.ai/observability/simulations-advanced#metrics-and-aggregations).

### Independent evaluation

Use three complementary checks:

1. Code for exact fields, permissions, actions, persistence, timing and arithmetic.
2. A fixed-rubric model judge for intent, invented commitments, correction handling, repetition, unsupported claims and summary faithfulness. Inputs include frozen account facts, transcript, tool requests/results and any authoritative final state. Output each criterion as pass/fail/uncertain with evidence; never silently score missing persistence or audio truth as a pass.
3. Human review to validate the reference labels and evaluate the judge itself. Record missed failures, false alarms and uncertainty per criterion. Include some apparently successful cases. A second reviewer/adjudication is particularly useful for critical disagreements.

The five existing post-call specialists remain product processing/QA components. They are not independent ground truth. A separate judging prompt or different model does not remove the need for calibration. Use development examples to refine the judge, then fresh reviewed validation cases; the already visible challenge scripts are not an unseen holdout. [Official OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

## Milestone 2: workflow and recovery details

- Pick a durable application state store supporting the intended process layout. A small single-instance pilot can use persistent SQLite with strict serialization; independently running web/worker processes or multiple instances need shared transactional storage and coordination. Choose and document one topology, then test its actual guarantees. Google Sheets can remain a CRM-facing sink; it is not a transactional job queue.
- Make call registration automatic and trusted, before the model can access sensitive tools. Implement the agreed recipient-verification policy and test wrong-party/unknown-account cases. Revisit the opening's early debt disclosure as an explicit product-policy change, not a silent benchmark edit.
- Persist incoming end-of-call events, deduplicate them, and acknowledge only after durable acceptance. Jobs need state, retry limits, leases/recovery for abandoned processing, and a visible failed-job queue. Reconcile external side effects after timeouts instead of blindly repeating them.
- Reconcile in-call receipts and post-call extraction using stable IDs. A corrected promise must not leave two active conflicting records. An opt-out must persist even if post-call analysis fails. Unverified payment claims never modify the payment ledger.
- Keep task acceptance and completion separate, with an accountable owner, due target and resolution evidence. Record-review exceptions and operational follow-up remain distinct queues/concepts.
- Test privacy boundaries, wrong-account arguments, duplicate deliveries, out-of-order events, server restart, tool rejection and lost acknowledgements using offline/integration fixtures before spending on audio calls.

## Milestones 3–4: test and release gates

The caller cards and review guide are already available: [scripts](CALL_TEST_SCRIPTS_V2.md), [review process](CALL_REVIEW_GUIDE_V2.md). Start with full promise, uncertain promise, already-paid claim and wrong person. Expand once these are reviewed; do not rush through all calls and fill labels afterward.

The application currently disables audio recording. Establish an explicit private test-only audio capture/review procedure with the participating tester's agreement and appropriate retention/access controls. Keep the public demo's recording behaviour and disclosure accurate. If only transcript/live notes exist, mark original audio truth unavailable and limit the claims accordingly.

The first release gates are:

- No unresolved observed critical failures in the declared release cases: wrong-party amount/identifier disclosure, wrong-account actions, unsupported payment promises, ignored opt-outs or false completion claims. Any uncertain critical result requires investigation. This is not a claim of zero production risk.
- Material records and required actions match reviewed expectations; disagreements are inspected rather than explained away by confidence scores.
- Duplicate/restart/retry drills preserve events, suppressions and pending work without duplicate active business records.
- Latency and cost are reported with counts, configuration, missingness and tested conditions. Product targets and operational ownership are explicit.
- Operator can inspect evidence, correct/hold a proposed record, resolve assigned work and explain the audit history. Public visitors cannot access customer/operator data.
- Persistent storage, restore/reconciliation procedure, readiness checks, bounded spending, dispatch stop control and rollback have been exercised.

Monitor processing failure rate, pending/overdue job age, tool failures, opt-out persistence, latency tails, spend and operator backlog. Alert on actionable conditions with an owner; a dashboard alone is not an incident response process. Establish transcript/log redaction and retention before real customer use.

The checked-in `render.yaml` uses the free plan. Render documents idle spin-down and loss of local filesystem changes across restarts/redeploys; its free-service documentation explicitly excludes production use. Verify actual hosting before choosing a durable layout, and present hosting costs before an upgrade. [Render free-service limits](https://render.com/docs/free).

## Milestone 5: design revamp brief

Design around two audiences with distinct permissions:

| Audience / view | Questions the design must answer |
|---|---|
| Recruiter overview | What business problem is solved? What did the owner decide and build? What is measured and what remains unproven? |
| Guided demo | What context does the agent have? What should I say? What happened during and after the call? |
| Call evidence | Which customer statement led to which tool, receipt, record, review finding and next action? |
| Evaluation view | What dataset/version was tested? What passed/failed? What are the denominators, examples and evidence limits? |
| Operator workspace | What needs attention, why, who owns it, and what evidence supports correction/approval/completion? |

Use accurate summary-to-detail navigation. Display transcript moments, tool arguments/results, saved records and timing together where possible. Separate accepted/pending/completed/failed states visually and in language. Put confidence beside its meaning, not as a substitute for correctness. Keep synthetic examples and real measured results identifiable.

Process: agree information architecture and the two key journeys; produce wireframes; establish visual direction; implement; check desktop/mobile, keyboard access, readability and contrast; then verify success, failure, empty, loading, unavailable-data and budget-limit states. Use public synthetic operator examples and authenticated operational access, rather than exposing live account records in the portfolio.

Completion evidence: a visitor can explain the workflow after the guided demo, find a real failure and its consequence, and understand the boundaries of the metrics. An operator can resolve a representative task and inspect the audit trail. Publish a short PM case study explaining the label-quality correction, tradeoffs, measured change, limitations and next experiment.

## Budget and user involvement

Most initial work can reuse existing saved calls and offline fixtures. Reserve paid LLM judging and live audio for the reviewed cases that need them. Existing simulation caps and rerun restrictions remain in force; any new paid batch needs a concrete estimate and approval. More live audio and durable hosting may require additional budget; do not assume they fit inside the earlier $1–2 allowance.

The owner's participation is required to review distinct calls independently, settle product-policy ambiguities and approve the design direction. Do not invent their human labels. Interview timing determines which milestones must be demonstrated first; no completion date is promised before that constraint is known.

Immediate next deliverable: an existing-data latency report, a frozen release/eval contract and an offline-tested judge interface, while preparing the durable call-processing design. Do not begin the full visual redesign or launch paid experiments in order to make the foundation appear complete.
