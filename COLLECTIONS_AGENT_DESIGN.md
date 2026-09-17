# B2B Collections Voice Agent — product design

**Thesis:** collection calls fail from missing context and lost commitments, not from bad
talking. So the product is a context-assembly and commitment-capture system that happens to
use voice, not a talking robot.

**Headline metric:** promise-to-pay kept rate — of the commitments captured on calls, what
share were honoured by the promised date. Secondary: contact rate, structured-outcome rate,
disputes surfaced, and human-review rate.

---

## 1. Domain model

### Aging buckets drive the script

| Bucket | Posture | Objective of the call |
|---|---|---|
| Pre-due (T-3) | Courtesy | Confirm invoice received and approved, catch blockers early |
| 1–30 | Friendly | Get a date. Most recoverable money lives here |
| 31–60 | Firm | Specific promise, or a documented reason |
| 61–90 | Escalated | Promise or dispute, plus flag for credit hold |
| 90+ | Pre-legal | Do not automate. Route to a human |

The 90+ rule is a product decision, not a technical limit: those conversations carry legal and
relationship risk that an automated caller should not take on. Say so in the README.

### Why B2B invoices actually go unpaid

Most of the time it isn't refusal. The taxonomy your system should tag against:

`invoice_not_received` · `awaiting_internal_approval` · `po_mismatch` ·
`missing_documentation` (GST invoice, delivery proof, work order) ·
`quality_dispute` · `quantity_dispute` · `pricing_dispute` ·
`payment_run_timing` (the AP team pays on fixed cycles) · `cash_flow` ·
`wrong_contact` · `paid_already` (reconciliation failure on our side)

This taxonomy is the most under-appreciated part of the product. If 30% of calls come back
`missing_documentation`, the fix is in billing, not in collections. **Collections becomes a
diagnostic instrument for the order-to-cash process.** That's the product-sense argument.

### Promise-to-pay: the core record

A promise is only an asset if it's specific. Required fields:

```
ptp_id, account_id, invoice_ids[], amount_promised,
promised_date, payment_method (NEFT/RTGS/UPI/cheque/portal),
reference_given (UTR or cheque number, if already paid),
captured_at, captured_by (agent version), confidence,
status (open | kept | broken | partial | superseded)
```

If the call produces no date, no amount, or no method, it is **not** a promise. It's a
`soft_commitment` and gets tracked separately. Conflating the two is how collections dashboards
lie to management.

---

## 2. End-to-end flow

```
┌── PRE-CALL (code, no LLM) ──────────────────────────────────┐
│ AR aging sheet → suppression filter → priority score        │
│ → context pack assembled → call queued                      │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌── CALL (Vapi, deterministic branches) ──────────────────────┐
│ disclosure → authority gate → state facts → branch          │
│ live tools: lookup_invoices, record_ptp, log_dispute,       │
│             send_document, schedule_callback, mark_opt_out  │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌── POST-CALL (parallel orchestration) ───────────────────────┐
│  Outcome      Promise      Dispute      Compliance          │
│  Extractor    Validator    Classifier   & QA Reviewer       │
│      └───────────┴────────────┴─────────────┘               │
│                  Supervisor merges                          │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌── WRITE-BACK ───────────────────────────────────────────────┐
│ Call log row · account status · PTP register · dispute      │
│ ticket · follow-up email draft · exception queue · metrics  │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌── FOLLOW-THROUGH (scheduled, daily) ────────────────────────┐
│ PTP due → check payments → kept / broken → re-queue,        │
│ update reliability score, recompute the headline metric     │
└─────────────────────────────────────────────────────────────┘
```

### Stage 1 — Pre-call assembly (the part nobody builds)

This is pure code and costs nothing to run. It is also where most of the product value sits.

**Suppression rules — do not call if:**
- invoice is paid or partially paid since last sync
- an open dispute exists on the invoice
- an open promise exists and its date hasn't passed yet
- contact opted out, or is marked wrong-party
- we called this account within the cooldown window (default 5 working days)
- outside calling hours for the account's time zone
- bucket is 90+ (route to human)
- account is in an active payment plan and on schedule

Every suppression is logged with a reason. **"Calls we correctly did not make" is a metric**, and
a good one — it's the relationship-damage the system prevented.

**Priority score** (transparent and tunable, not a black box):
```
priority = balance_weight × bucket_weight × ptp_reliability_penalty × contactability
```

**Context pack** handed to the voice agent: contact name and role, invoice list with numbers,
dates and amounts, total outstanding, prior promises and whether they were kept, open disputes,
last contact date and outcome, payment terms, and the preferred spoken language.

### Stage 2 — The call

Deterministic where it matters. Model-driven only in phrasing and branch detection.

Hard-coded outside the model:
- opening disclosure (fixed first message, always identical)
- authority gate — if the person isn't in accounts payable or authorized, take the right
  contact's details and end
- opt-out handling — immediate acknowledgement, write-back, end call
- max duration 3 minutes, hard stop
- no amount is ever spoken that isn't in the context pack

### Stage 3 — Post-call orchestration

Four specialists, parallel, each with a typed output:

1. **Outcome extractor** → structured call outcome, taxonomy reason code, next action
2. **Promise validator** → is the PTP complete, in the future, ≤ outstanding, method valid?
   Downgrades incomplete promises to `soft_commitment`
3. **Dispute classifier** → reason code, which invoice, what evidence was requested, routing
   target (billing / sales / ops / logistics)
4. **Compliance and QA reviewer** → did the agent disclose, verify authority, stay inside the
   permitted statements, avoid promising discounts or threatening consequences? Scores the call

Supervisor merges. **Confidence-gated write-back:** anything below threshold, or any
disagreement between specialists, goes to the human exception queue instead of auto-writing.
That gate is your human-in-the-loop story, and it's honest — you're not claiming full autonomy.

### Stage 4 — Write-back (Google Sheets as the CRM)

Sheets, via the Google Sheets API with a service account. Free, and it demos beautifully because
a reviewer can watch rows appear.

| Tab | What it holds |
|---|---|
| `Accounts` | master: customer, contact, terms, credit limit, reliability score |
| `Invoices` | invoice-level: amount, due date, bucket, status |
| `Call_Log` | one row per call: outcome, duration, cost, recording link, QA score |
| `PTP_Register` | every promise, with status lifecycle |
| `Disputes` | reason code, routing, age, resolution |
| `Exceptions` | low-confidence calls awaiting human review |
| `Suppressed` | calls not made, with reason |
| `Metrics` | daily rollup for the dashboard |

**Idempotency matters:** write keyed on `call_id`, so a re-run never duplicates rows.

Follow-up email is **drafted, not sent.** Sending someone's customer an email without review is
not a demo you want to give.

### Stage 5 — Follow-through (what makes it a system, not a demo)

A daily job checks promises that came due against the payments feed and marks them kept,
partial, or broken. Broken promises re-queue at an escalated posture and lower the account's
reliability score.

Without this loop you have a call bot. With it, you have a closed-loop collections system and a
headline number. This is the single highest-value thing in the build.

---

## 3. How the agent should talk

Real collectors are **brief, specific, and unembarrassed.** The most common failure in LLM voice
agents is excessive warmth — apologizing, over-empathizing, filling silence. An accounts payable
clerk who takes forty of these calls a week wants the invoice number and the point.

Principles for the prompt:
- One question per turn. Then stop talking.
- Lead with the invoice number and amount, not with pleasantries.
- Read numbers slowly, digit-grouped, and confirm them back.
- Never apologize for calling. It undercuts the legitimacy of the request.
- Never fill a pause. Silence is the other person thinking or checking a system.
- Always close the loop out loud: restate the promise before ending.
- Match their brevity. If they answer in three words, don't reply in three sentences.
- Mirror their language choice (English / Hindi / Hinglish) if configured.

**Disclosure decision:** the agent states it's an automated call in the opening line. This
costs a little rapport and buys legitimacy, and it's defensible under most emerging AI
disclosure norms. Document the trade-off in the README — that's the kind of judgment call
interviewers probe.

### System prompt (v1)

```
# ROLE
You are the accounts receivable assistant for {{company_name}}. You are calling
{{contact_name}} at {{customer_name}} about overdue invoices. You are professional,
brief, and specific. You are not apologetic, not chatty, and not pushy.

# OPENING (already spoken — do not repeat)
The first message has already introduced you as an automated call from
{{company_name}}'s accounts team and disclosed recording.

# YOUR ONLY FACTS
{{context_pack}}
Invoices: {{invoice_table}}
Total outstanding: {{total_outstanding}}
Payment terms: {{terms}}
Prior promises: {{ptp_history}}
Open disputes: {{open_disputes}}

You may not state any number, date, or invoice reference that is not above.
If asked something not covered here, say you'll have someone follow up, and
call schedule_callback.

# GOAL, IN ORDER OF PREFERENCE
1. A specific promise to pay: an amount, a date, and a method.
2. A documented reason for non-payment, matched to a reason code.
3. The correct contact, if this person is not the right one.

A vague answer such as "soon" or "next week sometime" is not a promise.
Ask once for the specific date and amount. If they still won't commit,
record it as a soft commitment and move on. Do not ask a third time.

# AUTHORITY GATE
Before stating any invoice amount, confirm you are speaking to someone who
handles accounts payable for {{customer_name}}. If not, ask for the right
person's name and number, call schedule_callback, and end politely.

# CONVERSATION RULES
- One question per turn. Then stop.
- Read amounts and dates slowly. Confirm them back before recording.
- Never apologize for calling.
- Never fill silence. Wait.
- Keep turns under 25 words unless reading an invoice list.
- If they are hostile, stay level, offer to send details in writing, and
  close the call. Do not argue.

# HARD PROHIBITIONS
- Never offer, hint at, or negotiate a discount, waiver, or settlement.
- Never mention legal action, credit holds, agencies, or consequences.
- Never discuss the debt with anyone who has not passed the authority gate.
- Never dispute the customer's claim. Record it and route it.
- Never state or guess a payment status you have not been given.
- If they ask to stop being called, acknowledge, call mark_opt_out, end.

# BRANCHES
Already paid → ask for the reference number and date, call log_payment_claim, end.
Will pay → pin down amount + date + method, confirm back, call record_ptp.
Dispute → ask what specifically and what they need from us, call log_dispute.
Needs a document → ask which, call send_document.
Cannot pay now → ask when they expect to, and why, once. Record the reason code.
Wrong person → authority gate, call schedule_callback.
Wants a human → call schedule_callback with priority high, end.

# CLOSING
Restate the outcome in one sentence, confirm it, thank them, end.
```

### Tool schemas

```
lookup_invoices(account_id)                      → invoice list (read, idempotent)
record_ptp(invoice_ids[], amount, date, method)  → ptp_id
log_dispute(invoice_id, reason_code, detail, evidence_requested) → dispute_id
log_payment_claim(invoice_id, reference, paid_on) → claim_id
send_document(invoice_id, doc_type)              → queued (draft, not sent)
schedule_callback(name, phone, reason, priority) → callback_id
mark_opt_out(contact_id, scope)                  → confirmation
```

Every tool returns fast and never blocks the conversation. Writes are queued and reconciled
post-call, so a slow sheet write never causes dead air.

---

## 4. Features worth building because they're actually smart

1. **The suppression engine.** Most collections tools optimize for more calls. This one
   optimizes for the right calls, and reports the ones it deliberately didn't make.
2. **Promise reliability scoring.** Each account accumulates a kept-rate. A customer who keeps
   every promise gets a lighter touch; one who breaks them gets earlier, firmer contact. Earned
   from behaviour rather than set by a rule.
3. **The non-payment taxonomy as an ops diagnostic.** Weekly rollup of *why* invoices aren't
   paid, routed to the team that can fix it. Turns collections from a cash chase into process
   feedback.
4. **Confidence-gated write-back.** The system knows when it isn't sure, and says so instead of
   writing bad data into the ledger.
5. **Dispute-to-evidence auto-drafting.** When a dispute cites a missing document, the follow-up
   email is pre-drafted with the right attachment named.
6. **"Calls we didn't make" and QA scoring as first-class metrics**, not an afterthought.

---

## 5. The PM artifacts (as important as the code)

1. **PRD** — problem, users (AR analyst, finance head, AP clerk on the other end), the
   agent-vs-workflow decision and why voice is deterministic, success metrics, non-goals.
2. **Eval report** — 40 test cases across branches and failure modes, with pass rates.
3. **Metric definitions memo** — what counts as a promise, what counts as contact, how kept-rate
   is computed. Unglamorous and the most senior-looking document in the set.
4. **Guardrail and escalation policy** — what's hard-coded and why, the 90+ human-only rule,
   the disclosure decision.
5. **Failure log** — what broke and what you changed.

---

## 6. Build order

| Step | What | Cost |
|---|---|---|
| 0 | Google Sheet with realistic fake AR data (~25 accounts, ~60 invoices) | free |
| 1 | Pre-call engine: suppression, priority, context pack. Pure code, fully tested | free |
| 2 | Vapi assistant + tool stubs. Record 12-15 calls covering every branch | ~$3 |
| 3 | Pull transcripts to `fixtures/`. **Within 14 days — Vapi retention limit** | free |
| 4 | Post-call orchestration against fixtures. Four specialists + supervisor | ~$1 |
| 5 | Sheets write-back, idempotent, with the exception queue | free |
| 6 | Follow-through job + metrics tab + dashboard | free |
| 7 | Eval harness over fixtures, then demo video | ~$2 |

Fixture discipline: after step 3, development touches no paid API.

---

## 7. The test call list (record these exactly)

1. Clean promise — specific date and amount
2. Vague promise — "soon", agent must pin down or downgrade
3. Already paid, with a reference number
4. Dispute: quantity mismatch
5. Dispute: missing GST invoice
6. Awaiting internal approval, no date available
7. Wrong person — authority gate must trigger
8. Asks for a discount — must refuse cleanly
9. Hostile caller — must stay level and close
10. Asks to stop calling — opt-out path
11. Partial payment offer
12. Asks a question outside the context pack
13. Very short answers throughout
14. Interrupts and talks over the agent
15. Asks whether it's a human

Cases 7-15 are the ones that produce your failure notes, which is the section reviewers read
first.
