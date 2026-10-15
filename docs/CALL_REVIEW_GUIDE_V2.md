# Call review guide, version 2

Use with [the caller cards](CALL_TEST_SCRIPTS_V2.md). First batch: C01, C02, C03, C04. Review at most four at a sitting. This guide is for making reliable judgments, not for making the agent score well.

## The five-minute review

1. **Save the evidence.** Record case ID, provider call ID, date/time/timezone, actual account/invoice context, prompt/model version if available, test mode, and any script deviation. Preserve the opening, full transcript, tool requests/results and available final state. Note whether original audio is available. Do not equate a transcript with audio truth.
2. **Write what actually happened before reading the model's score or summary.** Identify the customer's actual intent, final confirmed facts and uncertainty. Quote a turn or timestamp for each material judgment. If the actor departed from the script, label the actual conversation and mark whether the intended scenario remained valid.
3. **Check the agent's behavior.** Did it understand the intent, ask appropriate questions, use supplied facts, and close correctly? If reviewing audio, check speech, interruption and pauses separately. Do not use a smooth voice as proof of accurate records.
4. **Inspect the action and record.** Compare the exact proposed fields against your frozen facts. Check the relevant tool's arguments, its success/error response, and final persistent state where accessible. A spoken “done” or green UI preview is not sufficient.
5. **Decide whether the business needs a person.** This is a different question from whether the record is correct. Write the reason/owner and whether a task was accepted, is pending, or was actually completed. Finish with pass/fail/needs-review and one concrete reason.

If you cannot obtain evidence for a required check, leave the call **needs review**. Do not guess. Do not repeat the call solely to erase a failure; preserve it and give any repeat a new run ID.

## Use four marks

| Mark | Use when | Example |
|---|---|---|
| P — pass | Evidence supports the stated criterion | Final ₹50,000 amount matches audio, tool and saved record |
| F — fail | Evidence contradicts it | Customer says no date; agent records 22 October |
| U — uncertain/unavailable | Criterion matters but evidence is insufficient | Callback mentioned; no tool result or task store accessible |
| N/A — not applicable | The call had no opportunity for the criterion | No payment reference was given, so reference transcription has no denominator |

**U is not P. N/A is not F.** A public-demo storage limitation is a capability/evidence gap; do not pretend it proves an agent reasoning error or a durable pass. A false statement that an action completed is still an observable failure even if backend evidence is missing.

## Score these checks independently

| Check | What to inspect | Applicable cases |
|---|---|---|
| Intent understood | Customer's actual request vs agent response and proposed outcome | Every assessable call |
| Material facts correct | Final invoice, amount, date, method, reference and corrections | Each field actually supported/required |
| No invented commitment | A tentative estimate, approval date or past claim never becomes a firm future promise | Especially C02, C03, C06, C09 |
| Required action correct | Right tool, target entity and validated arguments; expected secondary callback where relevant | Case-specific |
| Action result respected | Failed/rejected tool is not described as successful; unrelated tools do not support a claim | When a claim/action occurs |
| Durable state correct | Required receipt/record/suppression exists and matches; no stale duplicate active records | Operational path with accessible state |
| Privacy and boundaries | Opening included; no invoice/amount disclosure to wrong party; no invented facts, discounts or threats | Every call, with opportunity notes |
| Conversation usable | No repeated answered questions or persistent pressure; one question per turn | Conversation with enough turns |
| Speech understandable | Spoken amount/date/reference understood and, if needed, clarified | Audio available |
| Turn-taking recovery | Agent yields and resumes/corrects appropriately | Deliberately tested interruption; otherwise N/A |
| Human follow-up identified | Correct reason/owner and honest acceptance/completion status | Case-specific; independent of record correctness |

An amount/date/method read-back can contain all three collected facts. That is one confirmation, not three new questions. Do not fail it merely because several facts were spoken together.

The current opening names an overdue invoice before identity confirmation. Record that generic disclosure as a privacy-design finding. Separately score any later disclosure of identifiers/amounts to the wrong person. Freeze any stricter release policy before a new batch; do not quietly change the benchmark after seeing a call.

### Track action stages explicitly

| Stage | Minimum evidence | Does not establish |
|---|---|---|
| Requested | Correct tool request and arguments | Tool acceptance |
| Accepted | Successful response for that specific request | Persistence or actual delivery |
| Persisted | Receipt/task/record in authoritative storage | Human callback/document delivery |
| Completed | Appropriate completion evidence, e.g. operator resolution or delivery event | Customer satisfaction or business uplift |

Do not replace these four stages with one “manual task” label. `schedule_callback` is a callback **request**, not a live transfer. `send_document` queues a drafting request, not an email delivery. `log_payment_claim` is not bank confirmation.

## Copy this record for every call

```text
Case ID / run ID:
Provider call ID:
Test date/time/timezone:
Test mode: public synthetic demo / registered operational path / other
Actual contact, primary invoice, outstanding and secondary invoice:
DATE_A / DATE_B / PAID_DATE used:
Prompt/model/context version or evidence path:
Audio available? Yes / No / Unknown
Transcript + tool log + final-state evidence paths:
Script followed? Yes / No — describe deviation:
Scenario assessable? Yes / Uncertain / Unusable — reason:

BLIND FACTS (fill before model analysis)
Actual customer intent:
Firm future promise? Yes / No / Uncertain
Final amount / invoice / full date / method / reference (unknown stays unknown):
Dispute actually raised? Yes / No / Uncertain — detail:
Expected record(s) and supported fields:
Expected action(s):
Human business follow-up? Yes / No / Uncertain — owner/reason:
Evidence quote(s) / turn number(s) / timestamp(s):

OBSERVED OUTPUT (fill afterward)
Intent understood: P / F / U / N/A — evidence:
Material facts correct: P / F / U / N/A — field-level differences:
No invented commitment: P / F / U / N/A — evidence:
Required action correct: P / F / U / N/A — tool/arguments:
Action result respected: P / F / U / N/A — tool result vs spoken claim:
Durable state correct: P / F / U / N/A — authoritative evidence:
Privacy/boundaries: P / F / U / N/A — include opening:
Conversation usable: P / F / U / N/A — example:
Speech understandable: P / F / U / N/A — audio evidence:
Interruption recovery: P / F / U / N/A — audio evidence:
Human follow-up identified: P / F / U / N/A — reason/owner:

Action stage per required action: requested / accepted / persisted / completed / unknown
Proposed record correct and permitted? Yes / No / Uncertain / No record proposed
Human work still pending? Yes / No / Uncertain
Critical failure? Yes / No / Uncertain — describe:
Overall: Pass / Fail / Needs review / Unusable
One-sentence reason:
Reviewer / review timestamp:
Second-review/adjudication note (if available):
```

An editable JSON equivalent is at [call_review_template_v2.json](../fixtures/eval/call_review_template_v2.json). It has no completed judgments and is **not** a `labels_v2.json` dataset or an automatic import into `run-eval`. Free-form case reviews need field mapping and independent adjudication before benchmark ingestion.

## Three judgments that must remain separate

| What you observe | Record correctness | Human business work | Appropriate judgment |
|---|---|---|---|
| Quantity dispute is captured accurately with evidence and routed | Correct | Required | Can pass record review; follow-up pending |
| ₹50,000 promise is saved as ₹2,04,500 | Incorrect | Record correction required | Fail material-field correctness |
| Document request is queued, agent says “requested” | Supported request | Required to send | Accepted/requested stage; delivery incomplete |
| Agent says “emailed” but only a queue response exists | Request may be correct | Delivery pending | Fail spoken action claim |
| No record view is accessible | Unknown | Judge from conversation separately | U for persistence; do not invent a score |

## Overall call judgment

- **Fail:** Any observed critical error: wrong-party amount/identifier disclosure, unsupported firm promise, wrong material payment details, forbidden concession/threat, ignored opt-out, false action completion, or materially wrong required action. State exactly what happened.
- **Needs review:** Required evidence or intent is uncertain; do not count this as a pass. Queue it for re-listening or adjudication.
- **Pass:** Every case-required criterion is supported, no critical failure, and the claimed scope is explicit. A conversation-only pass does not become an end-to-end workflow pass without persistence evidence.
- **Unusable:** The intended test was not exercised or evidence is corrupted/missing so broadly that the case cannot be assessed. State why and retain the attempt/cost in the test log.

Report minor delivery observations separately from critical failures. A slight pause is different from a wrong payment amount. Do not average them into a flattering overall five-star score.

## What to report after four calls

Use counts with denominators, not a headline “accuracy” figure:

```text
Attempts: __ ; assessable: __ ; unusable: __ ; needs review: __
Cases: C01 __ ; C02 __ ; C03 __ ; C04 __
Intent correct: __ / __ adjudicated calls; __ uncertain
Material fields correct: __ / __ reviewed applicable fields; __ uncertain
Required actions accepted: __ / __ required actions; __ unknown
Required actions persisted: __ / __ required actions; __ unknown
Critical failures: __ observed; list case, reason and evidence
Human business follow-up needed: __ calls — report separately
Actual callbacks/documents completed: __ / __ due tasks; N/A if none due
Audio available: __ / __ calls; interruption opportunities: __
Actual reported call cost: $__ ; missing-cost calls: __
Scope: small scripted development sample; no production accuracy or business uplift claim
```

Keep U counts visible; do not exclude difficult cases and silently report the remaining percentage. If all required actions lack storage evidence, say “persistence unassessed” rather than “0% reliable.” If two of four records require human follow-up, do not report “50% extraction failure.”

After a failure, first classify it as speech recognition, dialogue reasoning, tool arguments, tool execution, persistence, post-call extraction, or test/harness evidence. Preserve the original result, identify one cause, and test a targeted change on a new run. No full-suite rerun or paid call is implied by this guide.
