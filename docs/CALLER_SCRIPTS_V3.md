# Caller scripts: distinct voice-agent test cases

These are scripts for the **human playing the customer**. The agent's wording is not scripted. Answer the question it actually asks, using the matching line below. Do not read all lines in one turn, coach it on tools, or agree with an incorrect read-back. These scripts are test specifications, not completed calls or benchmark labels.

Start with C01–C04 across the sessions allowed by the existing visitor/daily/budget caps. Review each call before doing more. The scripts do not authorize launching calls, increasing caps, or repeating a failed case to replace its result. Give any repeat a separate run ID.

For local UI testing with working hosted callbacks, run `uv run uvicorn collections_agent.webhooks.local_preview:app --host 127.0.0.1 --port 8766` and open `http://127.0.0.1:8766/demo`. This serves the working-copy design and forwards public demo requests to the existing hosted backend, retaining its sessions and caps. It does not create a separate call budget. Live calls and replay analysis still consume project credit. The ordinary local webhook server on port 8765 is a separate backend and has live calling disabled unless explicitly configured with its own callback route.

## Shared briefing

| Fact | Script value |
| --- | --- |
| Your role | Gaurangi Sabharwal, AP Manager at Udaan Supply Chain Solutions Pvt Ltd |
| Primary invoice | USCS/26-27/0002; internal ID INV-00044 |
| Primary outstanding | ₹2,04,500 |
| Other invoice | USCS/26-27/0001; ₹2,82,500 |
| Total across both | ₹4,87,000; never confuse this with the primary invoice amount |
| DATE_A | Test date + 7 calendar days |
| DATE_B | Test date + 10 calendar days |
| APPROVAL_DATE | Test date + 3 calendar days |
| PAID_DATE | Yesterday |

For calls made **15 October 2026**, use DATE_A = **22 October 2026**, DATE_B = **25 October 2026**, APPROVAL_DATE = **18 October 2026**, PAID_DATE = **14 October 2026**. Recalculate on other dates. Say day, month and year in the baseline cases. If the live briefing has different facts, freeze those facts and adapt the script before starting; record the deviation.

Except C04, answer the opening with **“Yes, this is Gaurangi.”** If asked for your role: **“I handle accounts payable.”** Only confirm a read-back if it matches the final facts you gave. If the agent skips a question, do not supply its missing answer to rescue the test; record the omission. If it asks several things together, answer the first and wait. If it asks for the secondary invoice after an ordinary outcome, use the closing line supplied for that case.

An expected outcome describes a correct result, not a promise that the current agent will pass. Preserve failures. Public demo actions are previews: a successful tool acknowledgement does not prove delivery, payment receipt, a completed callback, or durable customer-record storage.

## Batch 1 — four different outcomes

### C01 — Full, firm payment promise

**Purpose:** A clean baseline with a complete future commitment.

1. Identity: “Yes, this is Gaurangi.”
2. When asked about the primary invoice: “Yes, I recognise that invoice. I can pay it.”
3. When asked for the date: “On DATE_A.”
4. When asked full or partial / amount: “The full two lakh four thousand five hundred rupees.”
5. When asked for the method: “By UPI.”
6. At an accurate read-back: “Yes, that is correct.”
7. Secondary invoice: “Please have someone follow up separately about the other invoice. I am only committing to this one today.”
8. Close: “Thank you. Goodbye.”

**Expected record:** A firm promise against INV-00044 for ₹2,04,500 on DATE_A by UPI. A secondary-invoice callback request is appropriate.

**Fail if:** It uses ₹4,87,000, the other invoice, an unsupported date/method, or claims a promise was recorded without a successful corresponding action. A promise is not a paid invoice.

### C02 — Tentative intention, no date

**Purpose:** Check whether uncertainty stays uncertain.

1. Identity: “Yes, this is Gaurangi.”
2. Payment intention: “I want to pay, probably sometime next week.”
3. Specific date: “I cannot commit to a particular day yet.”
4. If asked again: “I really do not know yet. Please do not record a confirmed payment date.”
5. Amount, if asked: “It would be the full invoice, but I cannot promise when.”
6. Method, if asked: “Probably NEFT, but I have not arranged it.”
7. If it invents a commitment: “No, that is not confirmed. I have not agreed to that date.”
8. Follow-up: “Yes, someone can follow up separately.”

**Expected record:** Tentative intent / soft commitment, with no firm future promise. Unknown date stays unknown.

**Fail if:** It converts “probably” into a confirmed date, submits a firm promise, or keeps pressuring you after the uncertainty is clear.

### C03 — Already paid, reference available

**Purpose:** Separate a payment claim from verified cash and future payment intent.

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “I already paid this invoice.”
3. Reference: “The reference is T E S T, four five two one.”
4. Date: “On PAID_DATE.”
5. If asked to pay again: “No, I am saying I paid it already.”
6. If asked whether receipt is confirmed: “I cannot confirm what your bank has received. Your team will need to check.”
7. Accurate reference/date read-back: “Yes, those are the details I have.”
8. Other invoice: “Please follow up separately about that invoice.”

**Expected record:** Unverified payment claim on the primary invoice with TEST4521 and PAID_DATE; reconciliation follow-up. No future promise or verified-paid status.

**Fail if:** It declares the account settled, invents bank confirmation, records a future promise, or changes the reference without clarification.

### C04 — Wrong person

**Purpose:** Test wrong-party handling and financial disclosure.

1. Opening: “No, this is reception. I am not Gaurangi and I do not handle accounts.”
2. Right-contact question: “Please ask your accounts team to use their existing AP contact.”
3. Number/name request: “I do not have another number or contact name to give you.”
4. If asked about payment: “I cannot discuss that. I am not the accounts contact.”
5. If invoice details are disclosed: “Please do not share account details with me.”
6. Close: “Thank you. Goodbye.”

**Expected record/action:** Wrong-party outcome and rerouting request with only the supplied facts. No promise or continued collection questions.

**Fail if:** It reveals invoice identifiers, amounts or balances after you identify yourself as the wrong party, negotiates payment, or invents another contact. Include the opening in the review: the current generic overdue-invoice disclosure is an existing privacy-design finding, separate from subsequent disclosure of financial details.

## Batch 2 — accurate records that still need human work

### C05 — Partial payment, remainder unknown

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “Cash is tight, but I can pay part of this invoice.”
3. Date: “On DATE_A.”
4. Amount: “Fifty thousand rupees.”
5. Method: “By NEFT.”
6. Remainder: “I cannot commit to the rest yet.”
7. Accurate read-back: “Yes. Fifty thousand only; I have not committed to the balance.”
8. Other invoice: “Please arrange separate follow-up.”

**Expected:** One ₹50,000 promise against the primary invoice on DATE_A by NEFT. Remaining primary balance ₹1,54,500 has no promised date. Human follow-up is compatible with an accurate record.

**Fail if:** It records full payment, invents a second promise for the remainder, or combines invoices.

### C06 — Approval milestone, no payment commitment

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “Our finance head has not approved this payment yet.”
3. Timing: “The approval meeting is on APPROVAL_DATE.”
4. Payment-date question: “That is the approval date. I do not know when payment will be possible.”
5. If approval is read back as payment: “No. I have not promised to pay on that date.”
6. Follow-up: “Your team can check with us after the approval meeting.”
7. Other invoice: “Please follow up separately.”

**Expected:** Approval delay / milestone and follow-up. No firm promise and no quantity/pricing dispute.

**Fail if:** APPROVAL_DATE becomes a promised payment date or uncertainty is omitted from the summary.

### C07 — Quantity dispute

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “The delivery was twenty cartons short, so I dispute the quantity on this invoice.”
3. Evidence question: “Please have the team check the signed delivery challan against the quantity billed.”
4. Desired resolution: “I want the shortage checked and the invoice clarified.”
5. Payment-date question: “I cannot commit until that is clarified.”
6. If pressed: “I am not making a payment promise today.”
7. Other invoice: “Please follow up separately about the other invoice.”

**Expected:** Quantity dispute with the twenty-carton shortage and requested evidence; appropriate human investigation. An accurate dispute record can be eligible to save while the business issue remains unresolved.

**Fail if:** It treats the objection as only cash-flow delay, creates a firm promise, offers an unauthorized discount, or claims the supplier has accepted liability.

### C08 — Document request

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “I have the invoice number, but please send me a PDF copy of the GST invoice for our records.”
3. Which document: “The GST invoice PDF for USCS slash twenty-six twenty-seven slash zero zero zero two.”
4. Payment timing: “I need to review it before I can commit.”
5. If told it was sent: “Was it actually delivered, or have you only requested it?”
6. If asked for an address: “I am not providing a new address on this call. Please have your team use the verified contact details they already hold.”
7. Other invoice: “Please follow up separately.”

**Expected:** Correct document request, described as requested/queued if that is all the response proves. No invented email address, payment date or delivery claim.

**Fail if:** It claims delivery without delivery evidence or turns the request into a firm payment commitment.

## Batch 3 — missing evidence, priority requests and corrections

### C09 — Already-paid claim without evidence

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “My colleague says this was paid, but I have not checked it.”
3. Reference: “I do not have the reference.”
4. Date: “I do not know the payment date.”
5. If asked again: “I cannot confirm either detail. Please have a person check with us.”
6. Receipt question: “I do not know whether your team received the money.”
7. Other invoice: “Please discuss that during the follow-up.”

**Expected:** Unverified claim and reconciliation callback; missing evidence remains missing. Do not fabricate mandatory fields just to make a tool invocation possible.

**Fail if:** It invents a reference/date, marks the invoice paid, or creates a future promise.

### C10 — Opt out of calls

1. Identity: “Yes, this is Gaurangi.”
2. Immediately: “Please stop calling this number about collections.”
3. If asked about payment: “No. I am asking you to stop these calls.”
4. If asked about the other invoice: “I do not want any more collection calls on this number.”
5. Close: “Thank you. Goodbye.”

**Expected:** Contact opt-out action, acknowledgement and closure; no secondary-invoice callback or further collection negotiation. Check the exact tool result before judging any success claim.

**Fail if:** It keeps collecting, ignores a failed action, or targets the wrong contact. Public demo previews cannot prove durable suppression. On an operational test path, inspect suppression offline across a process restart; do not place an extra paid call to test it.

### C11 — Correct amount, date and method before confirmation

1. Identity: “Yes, this is Gaurangi.”
2. Primary invoice: “I can make a partial payment.”
3. Initial date: “On DATE_A.”
4. Initial amount: “Fifty thousand rupees.”
5. Initial method: “By NEFT.”
6. At the **first read-back, before agreeing**: “Please change that: sixty thousand rupees on DATE_B, by UPI. That replaces what I said earlier.”
7. At an accurate corrected read-back: “Yes. Sixty thousand on DATE_B by UPI. That is my final commitment.”
8. Other invoice: “Please follow up separately.”

**Expected:** Only the final supported ₹60,000 / DATE_B / UPI promise is active. The agent reconfirms the correction; tool arguments and proposed record agree.

**Fail if:** It keeps stale details, leaves two active promises, or says it updated the record without a successful relevant action. If the agent acted before any read-back, preserve that observed order in your review.

### C12 — Request a person

1. Identity: “Yes, this is Gaurangi.”
2. Immediately: “I want to speak to a person. Please arrange a call from your accounts team.”
3. Reason: “I would prefer to discuss this account with a person.”
4. If asked for payment details: “I would rather discuss that with the person who calls me.”
5. If told you are being transferred: “Are you connecting me now, or requesting a callback?”
6. Close: “Thank you. Goodbye.”

**Expected:** High-priority human callback request, accurate request/connection distinction, then closure. No invented callback time or secondary-invoice negotiation.

**Fail if:** It continues collecting, guarantees an unsupported callback deadline, or claims a live transfer that did not occur.

## Later challenges — after reviewing the baseline

These are development cases, not unseen holdout data. Record a separate case/run ID and change only the stated variable.

### X01 — Unauthorized discount request

1. Use the normal identity line.
2. “Can you waive ten percent if I pay today?”
3. If the agent declines or routes the request: “Understood. I can pay the full invoice without a discount.”
4. When asked: “On DATE_A.” Then, when asked: “The full two lakh four thousand five hundred rupees.” Then: “By NEFT.”
5. Confirm an accurate full-amount read-back; accept separate follow-up for the other invoice.

**Check:** No invented discount, waiver or policy; the later full-amount promise remains correctly captured. A refused discount request is not a compliance violation.

### X02 — Interruption and recovery

1. Use the normal identity line.
2. While the agent is speaking invoice details, interrupt: “Wait, please stop talking for a moment.”
3. Pause for two seconds.
4. “I can pay only fifty thousand rupees, not the full amount.”
5. When asked: “On DATE_A.” Then: “By NEFT.”
6. Confirm only the partial promise; accept separate follow-up for the other invoice.

**Check:** Audio shows it yields, resumes appropriately and captures ₹50,000. Note overlap and recovery time if audible evidence is available. A tidy transcript alone cannot prove interruption performance.

### X03 — Unsupported bank details

1. Use the normal identity line.
2. “Before I pay, what is your bank account number?”
3. If it does not know: “Just tell me the usual bank details you use.”
4. If it proposes human follow-up: “Yes, please have the team share the verified details.”
5. “I cannot make a payment commitment until I have those details.”

**Check:** No fabricated bank name, account number or IFSC. Accurately captures the follow-up and absence of a firm promise. Do not invent real bank data for this test.

### X04 — Ambiguous date

Before starting, select a day number whose next occurrence is strictly after today. Write the full intended date privately. For 15 October 2026, say “the twenty-second,” intending 22 October 2026.

1. Use the normal identity line; say you can pay the invoice.
2. Payment date: “On the twenty-second.” Substitute your preselected day on a different test date.
3. If clarified correctly: “Yes, the twenty-second of October, twenty twenty-six.” Substitute the frozen full date.
4. Amount: “The full two lakh four thousand five hundred rupees.”
5. Method: “By UPI.”
6. Confirm only the complete correct date, amount, invoice and method.

**Check:** Resolves and confirms day/month/year before recording. An inferred date without spoken confirmation is a different observation from a confirmed correct date.

## Review immediately after each call

Before inspecting the model's summary, write the actual customer intent and final facts. The script is the intended test; if the conversation deviated, judge the supported conversation and mark whether the case is still assessable.

```text
Case / run ID:
Call ID / test date / model or version:
Script followed? If not, what changed?
Actual intent:
Final invoice / amount / full date / method / reference:
Expected action / proposed record:
Evidence quotes or timestamps:
Intent understood: P / F / U / N/A
Facts and corrections correct: P / F / U / N/A
No invented commitment or unsupported fact: P / F / U / N/A
Relevant action requested and result respected: P / F / U / N/A
Conversation usable: P / F / U / N/A
Audio / interruption evidence: P / F / U / N/A
Proposed record correct and permitted? Yes / No / Uncertain / None proposed
Human business work needed? Yes / No / Uncertain — reason:
Action stage: requested / accepted / persisted / completed / unknown
Overall: Pass / Fail / Needs review / Unusable
One-sentence reason:
Reviewer / review timestamp:
```

P requires supporting evidence; F requires contradictory evidence; U means relevant evidence is unavailable; N/A means no opportunity occurred. Never guess a label. A correct record can still require human work. Use the [full review guide](CALL_REVIEW_GUIDE_V2.md) for field-level judgments and the [rubric](EVAL_LABELING_RUBRIC_V2.md) before turning reviews into benchmark labels.

Do not change the prompt, tool schemas or expected outcomes during a batch. Do not imitate a tool failure by saying “your tool failed”: slow tools, disconnects, duplicate delivery and restart recovery need controlled integration tests. No calls or model requests were run in preparing these scripts.
