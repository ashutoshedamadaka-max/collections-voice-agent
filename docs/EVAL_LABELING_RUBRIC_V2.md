Evaluation labeling rubric, version 2

Prepared 15 October 2026. The initial evidence/uncertainty workflow is implemented in `label-calls` and `eval/labels.py`; no replacement human labels have been created. The richer structured intent, record and per-field action fields below describe the target rubric. The initial CLI captures expected records and follow-up rationale as text; record-level adjudication, second review and holdout management still require a reviewer workflow.

**Status of version 1**

The owner confirmed the nine historical calls were labeled randomly across multiple parameters. Treat all performance metrics derived from those labels as invalid. Preserve the original files as invalidated historical artifacts. Do not overwrite them or use them for model selection, confidence calibration, release gates, or portfolio accuracy claims.

**Three separate questions**

1. What happened in the conversation? Label facts and intent from evidence.
2. What records/actions are permitted and required under the product policy? Label the expected structured result.
3. Does the business case still need human work? Label operational follow-up separately.

An accurately captured dispute can be saved automatically and still require a human to resolve it. A vague intention can be saved as a soft commitment without being fabricated into a payment promise. Opt-out persistence should not depend on a post-call confidence gate. An inaccurate or unsupported proposed record needs correction regardless of whether the customer requires follow-up.

**Labeling procedure**

- Freeze the policy, source context, and rubric before reviewing output.
- Start with distinct canonical cases: full/partial promise, vague intent, approval delay, already paid with/without reference, invoice not received, pricing/quantity dispute, document request, wrong person, opt-out, escalation, and conflicting/corrected details. Cases may overlap only when the overlap is intentional.
- First label transcript facts and expected actions without showing model output. If audio exists, use it to distinguish speech-recognition errors from reasoning errors; otherwise mark audio truth unavailable.
- Record evidence spans or turn IDs for each material field. Use `uncertain` or `not_applicable` where appropriate; do not force a guess.
- Then compare the frozen expected record/actions with the system's actual output and durable state. A call-level “safe to write” label without specifying the record is too ambiguous.
- Use a second reviewer for critical cases and disagreements where possible. Adjudicate differences against the policy, not the model's confidence. Label in short sessions; never fill unreviewed rows with random defaults.
- Keep a holdout set after development. Do not repeatedly inspect and tune against every case.
- Store rubric version, label version, reviewer, timestamp, input hash, and adjudication reason. Record changes explicitly.

**Fields to label**

| Field | Meaning | Example |
|---|---|---|
| `intent` | Customer's actual intent; permit uncertainty/multiple intents where policy allows | already paid, future promise, dispute, document request |
| `authority_status` | Evidence that the contact meets the agreed verification policy | verified, wrong party, ambiguous |
| `promise_intent` | Explicit future payment commitment, vague intention, or none | “I paid last week” is not a future promise |
| `promise_fields` | Canonical invoice, amount, currency, date, method, and evidence for each | Unknown method remains null |
| `confirmation_status` | Whether the caller confirmed the relevant details; capture later corrections | corrected date invalidates prior confirmation |
| `required_actions` | Policy-required actions and target entities | callback for unverified payment claim |
| `permitted_records` | Expected record types and their supported contents | soft commitment plus follow-up task |
| `operational_followup` | Whether someone needs to act; owner, reason, and due window | billing resolves a dispute |
| `policy_violations` | Specific violation with evidence and severity | amount disclosed before required verification |
| `assessability` | Whether the evidence is sufficient | invalid simulator behavior, incomplete transcript |

Derived assessment of actual output:

| Result | Required evidence |
|---|---|
| Correct extraction | Material fields match the frozen label; no unsupported extra facts |
| Correct action | Right command, authorized target, validated parameters |
| Accepted action | Durable receipt exists; distinguish from completed human work |
| Accurate spoken confirmation | Claim matches the actual action and status at that point |
| Eligible automatic write | Proposed record is correct and permitted by policy; no blocking uncertainty |
| Successful handoff | Task durably assigned, or operator connected for a live transfer; a spoken promise is insufficient |

**Examples that prevent the original confusion**

| Conversation | Record handling | Human work |
|---|---|---|
| “The billed quantity is wrong” | Save supported dispute details and route them | Required to investigate/resolve |
| “I will pay soon” | Save a soft commitment if supported; no firm promise date | May be needed under follow-up policy |
| “I paid yesterday; UTR is …” | Save an unverified payment claim; do not mark the invoice paid | Reconcile against payment ledger |
| “Stop calling” | Persist suppression immediately | Separate audit/escalation policy, if any |
| Caller corrects amount/date | Use the final confirmed details; otherwise keep pending | Needed if uncertainty remains |
| Model invents a payment date | Reject/correct the proposed payment record | Review needed because the record is unsupported |

**Metrics after valid labels exist**

- Field-level correctness and missing/unsupported-field counts.
- Required-action completion, forbidden actions, and false completion claims.
- Safe-write precision: correct, policy-permitted proposed writes / reviewed proposed writes.
- Unsafe-write detection: correctly held invalid proposed writes / all reviewed invalid proposed writes.
- Unnecessary holds: correct, policy-permitted proposed writes held / all reviewed correct, permitted proposed writes.
- Follow-up identification and fulfillment, separately from write correctness.
- Reviewer agreement before adjudication, uncertain-label rate, sample size, and dataset coverage.

All denominators must be explicit. Report zero observed failures with the number of opportunities; do not call it proven zero risk. Show no data as `N/A`, not zero percent. Historical transcripts can be reused for debugging, but a rebuilt diverse benchmark is required for portfolio claims.
