# Building a collections voice agent with reviewable outcomes

Portfolio release · 15 October 2026 · English · synthetic demonstration data

## The product problem

An accounts-receivable team needs more than a convincing conversation. It needs to know what a customer committed to, which invoice the commitment concerns, what requires follow-up, and whether the money subsequently arrived. A fluent agent that invents a completed action or records an unsupported promise can make that workflow worse.

This project explores a bounded workflow: discuss the oldest eligible invoice, capture a supported outcome, and prepare a record that an operator can inspect. The intended users are an AR analyst managing follow-up and a collections lead measuring outcomes. It has not been validated with a real-customer business pilot.

## What I built

The application includes deterministic pre-call eligibility and prioritization, a Vapi voice assistant with invoice context, seven callable tools, five parallel post-call specialists, a code-based supervisor, Sheets write-back, payment follow-through, and evaluation tooling.

The public demo exposes a narrower path. A visitor plays the customer using synthetic account context, or selects a saved example. The transcript and tool events flow into post-call analysis. The interface shows the proposed outcome and record, with an explanation of whether record review is needed. It previews writes; it does not write visitor calls into a customer Sheet. Replays run fresh model analysis and consume project API credit.

The five specialists are separate model requests for outcome, promise, dispute, compliance and summary. They interpret provider transcripts; they do not independently listen to the audio. Code validates amounts, dates, invoice references and specific contradictions. The summary cannot authorize a write. Model confidence is uncalibrated and is not an accuracy probability.

## Decisions and trade-offs

| Decision | Product reason | Remaining limit |
| --- | --- | --- |
| Focus on one invoice | Finish a concrete outcome within a short call | Other invoices require follow-up |
| Put arithmetic and identity matching in code | Avoid deterministic errors hidden by fluent explanations | Speech recognition can still corrupt the input |
| Separate record review from business follow-up | A correct dispute record still needs an operator to resolve the dispute | Operator completion must be evidenced separately |
| Track accepted actions separately from completion | A callback request is not proof that someone called back | Public demo actions are previews |
| Keep saved examples alongside live calling | Let visitors inspect the workflow when live capacity is unavailable | Replay cannot validate audio interaction |
| Show missing evidence as unavailable | Prevent an unmeasured result from looking like a zero failure rate | Independent labels and controlled audio testing are pending |

## What the evidence supports

- Two saved demo calls contain 14 provider-reported turn measurements: median 1,493.5 ms and nearest-rank p95 2,579 ms. This is a small historical sample. Timing boundaries have not been independently checked at playback, and it is not a production latency claim. The [public aggregate](https://collections-voice-agent-demo.onrender.com/demo/evidence/latency) exposes no visitor transcript or call identifier.
- Historical text simulations produced 18/21 in-call passes on gpt-4o-mini and 17/21 on gpt-4o, in one run per model on 14 October 2026. Their respective recorded API costs were $0.0515 and $0.3780. These used evaluator v1; they exclude end-to-end post-call agreement and cannot establish a model ranking or audio success rate. The later evaluator has stricter checks and has not been rerun with paid calls.
- The original nine-call human benchmark was withdrawn because its labels were unreliable. Its agreement, false-write and false-hold results are invalid. Replacement annotation requires source evidence, uncertainty and independent judgments for record correctness and follow-up. There is no valid human-benchmark accuracy score yet.
- Offline regression tests cover deterministic validation, payment allocation, scoped tool receipts, duplicate handling, budget reservations and missing evidence. Passing them does not prove conversation quality or customer recovery.

## Failures that changed the implementation

Spoken invoice formatting caused exact matching to reject a valid promise. Model arithmetic incorrectly judged balances. An expired webhook left a caller in silence. Prompt branches contradicted the one-question instruction. These failures led to normalization, deterministic validation, reachability checks, timeout handling and more focused regression checks. The [failure log](FAILURES.md) records the investigations and their limits.

The evaluation process also failed: unreliable human labels created misleading metrics. Withdrawing those numbers and rebuilding the labeling process was necessary before making further quality claims. Payment follow-through now persists allocations to avoid using one payment to keep multiple promises; legacy results require reconciliation.

## How I would measure a pilot

Measure contact rate on attempted calls, record accuracy on independently reviewed proposals, human follow-up separately from extraction errors, and promise fulfillment only on a matured and reconciled cohort. Pair attributed recovery with cost per contact and operational workload. Report denominators, unavailable data and cohort definitions alongside results. See the [metric contract](metrics.md).

For conversation quality, combine exact code checks with controlled audio cases and a separate model judge calibrated against human decisions. Measure interruptions, unsupported commitments, corrections, summary faithfulness and time to an audible, useful response. The current operational specialists do not replace that independent judge. See the [evaluation method](eval.md) and [review guide](CALL_REVIEW_GUIDE_V2.md).

## What must happen before real-customer use

The current hosted demo is a portfolio prototype. A customer pilot still requires durable event processing and retries, verified account scope and recipient checks, operator ownership and evidenced closure, privacy and retention controls, restart recovery, alerts, a kill switch, and a reviewed audio benchmark. The free hosting configuration uses ephemeral local storage; local SQLite receipts do not survive every hosted redeploy. No verified recovery uplift, customer deployment, production availability or validated voice SLA is claimed.

The next product milestone is a bounded, observable pilot with explicit release gates. The [implementation status](IMPLEMENTATION_STATUS_2026-10-15.md) separates implemented foundations from remaining work, and the [roadmap](PILOT_AND_DESIGN_ROADMAP.md) sets the sequence.
