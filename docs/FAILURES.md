# Failure log

Real incidents hit during manual testing, and what changed as a result. Kept separate from
the design doc because these are things that actually happened, not things we planned for.

## 2026-09-16 — dead tool-call webhook produced silence, not an error

**Symptom:** During a `test-call --dial` web call, the caller gave a complete promise-to-pay
(full amount, a date, NEFT) after the agent asked for it. The agent never acknowledged it and
the call ended with `endedReason: silence-timed-out`. Nothing in the transcript or the local
webhook server's log suggested anything had gone wrong — no error, no stack trace, no failed
request. It looked exactly like the agent had frozen.

**Root cause:** The agent had almost certainly called `record_ptp` and was waiting on the
tool result before continuing — Vapi blocks the conversation on a sync tool call by design.
The assistant's `serverUrl` pointed at a Cloudflare quick tunnel from an earlier session,
which had died (the process that created it wasn't running; the hostname didn't even resolve
in DNS). Vapi had nowhere to deliver the tool call, so the model never got a result and never
spoke again. Separately, `serverUrl` (a bare string) turned out not to exist on Vapi's current
assistant schema at all — the field is `server.url` (an object) now — so this assistant's
routing was configured incorrectly independent of the tunnel dying.

**Why this is dangerous:** an unreachable tool-call endpoint fails *silently* from the
caller's perspective. There's no error to catch, no exception to log — just dead air that is
indistinguishable from the agent hanging or crashing. A caller mid-conversation has no idea
whether to wait or hang up, and every minute of silence is billed Vapi time.

**Fix:**
1. Re-pointed the assistant at a fresh tunnel using the correct `server.url` field
   (`voice/assistant_config.py`), not the nonexistent `serverUrl`.
2. **Reachability pre-check** (`voice/server_reachability.py`, wired into `test-call --dial`
   in `cli.py`): before dialing, fetch the assistant's configured server URL and GET its
   `/health` endpoint. Refuse to dial with a clear message if it doesn't respond, instead of
   letting a call proceed whose tools can't possibly work.
3. **Spoken fallback on timeout** (`voice/tool_schemas.py`'s `TOOL_TIMEOUT_MESSAGES`, attached
   to all 7 tools): Vapi's `request-response-delayed` message speaks a filler line if a tool
   hasn't responded within 4 seconds, and `request-failed` (fed to the model as a system hint)
   makes it acknowledge what the caller said and promise written confirmation if the tool call
   never resolves. `server.timeoutSeconds` was also tightened from Vapi's 20s default to 10s
   so that give-up happens well before the caller assumes the line is dead. This is defense in
   depth for production, not just this test setup — a webhook can go down for reasons other
   than a dev tunnel expiring.

**Lesson:** treat "the server is reachable" as something to verify before every call, not
something to assume because it worked last time — tunnels expire silently, and Vapi (like
most systems that block on a webhook) has no way to distinguish "no tool call was needed" from
"the tool call went nowhere." Both fixes matter together: the pre-check catches it before a
call starts, the spoken fallback catches it if the server dies *during* a call.

## 2026-09-16 — three findings from one dispute call

A single test call (dispute scenario, `endedReason: silence-timed-out`) surfaced three
unrelated problems at once. None of these are the tunnel/webhook issue above — that was
already fixed and confirmed reachable for this call.

**1. The model narrated the tool call instead of invoking it.** The transcript shows the
agent saying "I will, uh, log this as a dispute... for invoice 3281... quantity dispute" —
twice — and then continuing the conversation. There is no `tool_calls`-type message anywhere
in the raw payload, and `fixtures/tool_calls.jsonl` has zero entries for this call's window:
`log_dispute` was described in speech but never actually called. Checked whether Vapi exposes
a tool-choice/forced-function setting to prevent this at the transport level (like OpenAI's
`tool_choice: "required"`) — it does not; grepped the full current OpenAPI spec for
`toolChoice`/`tool_choice`/`forceFunction` and found nothing. Fix is instruction-based, at two
layers: every action tool's description in `tool_schemas.py` now opens with an explicit
trigger condition ("Call this immediately when...") and ends with `DO_NOT_NARRATE` ("Do not
say you will do this or describe doing it — call this function, then report the outcome");
`system_prompt.j2` adds the same rule as the first CONVERSATION RULE. If gpt-4o-mini still
narrates after this, the next step is gpt-4o — noted, not yet done, since it needs a fresh
call to confirm whether the prompt/description fix alone was enough.

**2. Incoming webhook request bodies were never logged.** Three `POST /vapi/tool-calls`
requests hit the server during the call above and all returned 200 — but with zero entries in
`tool_calls.jsonl` (which only gets written when a tool call is actually dispatched), there
was no way to tell whether they carried an empty `toolCallList`, a shape our parsing didn't
recognize, or something else. That blind spot made this incident partially unexplainable.
Fix: `webhooks/server.py` now logs the complete raw body of every `/vapi/tool-calls` request
to `fixtures/webhook_requests.jsonl` — a separate file from `tool_calls.jsonl` — before any
parsing or dispatch happens, so a request that resolves to "nothing to do" still leaves
evidence.

**3. Transcription quality.** The customer's answer to the agent's last question ("the
delivery challan") was never transcribed at all — it appears nowhere in `messages`,
`artifact.messages`, or the `transcript` field; the call just silence-timed-out afterward.
Separately, the transcriber consistently misheard structured invoice numbers: "KA-3281" came
back as "Camminis 3000 hundred 81" in one turn and "Quinus, 3281" in another. The live
assistant's transcriber turned out to be Deepgram nova-2 — the project's original low-cost
default from day one (`assistant_config.py`), not a "Cost Saver" downgrade from Soniox as
suspected; it had simply never been upgraded. Fix: switched to Soniox `stt-rt-v5`, a
materially higher-accuracy real-time transcriber, and voice to "Naina" (Vapi's built-in Indian
American accent voice) to better match the calling context. Also added a prompt rule to read
invoice numbers letter-by-letter with the prefix spelled out ("K, A, three-two-eight-one")
and confirm them back, rather than relying on the transcriber alone to get them right.

**Lesson:** "the tool call succeeded" (200 OK) and "the tool call happened" are not the same
claim — only the second one is what matters, and only logging raw requests (not just
dispatched ones) can tell them apart. Separately, a model narrating an action in speech is not
the same failure as it silently doing nothing — it sounds like progress to the caller and to
whoever is listening to the call, which makes it a more dangerous class of bug than a webhook
timeout, not a less dangerous one.

## 2026-09-16 — numbers are the agent's weakest speech moment (open, not yet fixed)

**Symptom:** Across multiple test calls, amounts and invoice numbers consistently come out
mumbled or hard to follow — e.g. "8 5 0 0. 0 0 0 0 0" for an amount, "Camminis 3000 hundred
81" and "Quinus, 3281" for the same invoice number in one call. Speech quality elsewhere in
the same calls (regular sentences) has been clear. This is bad for a collections agent
specifically, since amounts and invoice numbers are the entire point of the call — a caller
who can't parse what they owe or which invoice is in dispute can't act on the call at all.

**Not yet diagnosed:** whether this is TTS mishandling raw numerals, a connection artifact
during testing, or something else. Logged now so it's not lost; fixing it is a separate pass.

**Candidate fixes, for that pass:**
1. Pre-format numbers into words in the context pack, before they ever reach the prompt, so
   the voice layer never sees raw numerals — e.g. "four lakh fifty thousand rupees" instead of
   "450000", and "K-A three two eight one" instead of "KA-3281". Prompt-level instructions
   (letter-by-letter reading, added earlier today) only constrain how the *model* phrases
   things; they don't control how the *voice provider* renders whatever text it's given.
2. Check whether Vapi's voice config exposes SSML or a pronunciation dictionary for the
   current voice/provider (Naina, `vapi` provider) — if so, that may be a more direct fix than
   reformatting text.
3. Check whether reading two invoices back-to-back produces one long unbroken run of figures
   (per the transcript above, all figures for both invoices land in a single sentence) —
   splitting that into one sentence per invoice may help independently of numeral formatting.

## 2026-09-16 — two specialist bugs, one root cause: arithmetic delegated to a language model

**Symptom 1:** The promise validator's `amount_within_outstanding` field returned `false` for
a promise of 365,500 against invoices totaling exactly 85,000 + 280,500 = 365,500. The amount
was at the limit, not over it — the model's comparison was simply wrong.

**Symptom 2:** The compliance reviewer's rubric asked whether the agent stated a number "not
given as fact," which only catches fabrication. It does not catch a number that *was* given
but combined incorrectly — the agent stated two individual invoice amounts and then a total
that didn't match their sum, and compliance scored the call `qa_score: 1.0`, no violation
noted.

**Root cause (same for both):** asking an LLM to perform or verify arithmetic. A model that
got 85,000 + 280,500 ≠ 365,500 wrong has no reliable way to also grade whether its own sum is
correct — verifying arithmetic is arithmetic. This is the same class of bug in two different
specialists, not two unrelated bugs.

**Fix:**
1. **Promise validator split into extraction + validation.** The LLM (`extract_promise`) now
   only extracts what was said — `has_promise`, `amount`, `promised_date`, `method`,
   `invoice_ids` — and never judges completeness or compares against outstanding balances.
   `validate_promise_facts` (pure code, `postcall/specialists/promise.py`) computes
   `is_complete`, `is_future_dated`, `amount_within_outstanding`, `method_valid`, and
   `downgraded_to_soft_commitment` deterministically from the extraction plus real invoice
   data. No LLM call does arithmetic anywhere in this path anymore.
2. **Arithmetic-consistency check moved into compliance, in code.**
   `check_arithmetic_consistency` (pure code, `postcall/specialists/compliance.py`) extracts
   every "total ... <number>" the agent stated (regex, tuned to this project's own prompt
   phrasing) and compares it against the actual sum of outstanding balances from the real
   invoice data — not against anything the model said about itself. A mismatch is treated as
   a compliance violation: it caps `qa_score` at 0.5 regardless of the model's own qualitative
   score, and is recorded in `ComplianceReview.misstated_total`, a dedicated field, not buried
   in free-text notes.
3. **Confidence semantics fixed in every specialist's prompt.** The dispute classifier
   returned `confidence: 0.0` for a correct, unambiguous "no dispute" — it was conflating
   "how much was found" with "how certain I am." Every specialist's system prompt now states
   explicitly: confidence is certainty in the answer given, never a measure of how much was
   found; a confident "nothing here" is high confidence, not low.
4. **Supervisor merge changed from `min()` over all four specialists to `min()` over only the
   *material* ones** (`pipeline._material_confidences`). Outcome and compliance always count —
   every call gets a Call_Log row and a QA score. Promise and dispute only count when that
   specialist actually found something to write back (`has_promise` / `has_dispute` true).
   Combined with fix 3, a confidently-empty dispute classification on a clean call no longer
   drags an unrelated call through the exception queue.

**Lesson:** an LLM can extract facts from unstructured text far better than it can verify a
computation over those facts, even when the computation is trivial — and it will produce a
plausible-sounding wrong answer rather than an obvious failure, which makes this class of bug
easy to miss without deliberately testing against a case where the correct answer is known
(here: an amount exactly equal to, not less than, the outstanding balance). Wherever a
specialist's job includes a deterministic comparison, do the comparison in code and hand the
model only the facts and the computed result — never ask it to compute or double-check its own
arithmetic.

## 2026-09-16 — two follow-on bugs found by re-running the fix above

**1. The arithmetic-consistency check itself had a false positive.** Re-running the fixed
compliance check against a real (pre-Soniox) transcript produced `stated total(s) [3.0] do not
match the actual outstanding total 365,500.00` — nonsense. That transcript's numbers were
garbled digit-by-digit STT output ("3 6 5 5. 0 0 0. 0 0" for 365,500), and the regex grabbed
the lone leading "3" as if it were the whole figure. Treating that as a confirmed violation
would have been exactly the wrong lesson from the previous fix: a deterministic check is only
as trustworthy as the data it runs on, and a transcription artifact is not a stated fact.
**Fix:** `check_arithmetic_consistency` now requires a matched figure to clear
`MIN_PLAUSIBLE_CURRENCY_AMOUNT` (1,000) before treating it as a real stated total, and
introduces a third state instead of forcing pass/fail — "unverifiable" (no invoice data, or no
plausible figure found) returns `misstated_total: False` with an explanatory detail, not a
violation. An unverifiable check is not a failed check; flagging it as one would fill the
exception queue with noise nobody reads, which defeats the point of having a queue at all.

**2. A caught violation didn't actually block the write.** Before this fix, `misstated_total`
only capped `qa_score` — a number in a spreadsheet column — but the supervisor's gate
(`_disagreements` / confidence threshold) never looked at compliance findings at all. Call 2's
misstated total was correctly detected and scored, then auto-wrote anyway, because nothing
downstream of the compliance specialist actually checked for it. **Fix:** the supervisor
(`pipeline._hard_compliance_violations`) now tiers compliance findings explicitly, by product
decision, not by confidence: promising a discount/waiver, threatening consequences, failing
the authority gate, stating a fact outside context, or misstating a total/amount always force
`exception_queue`, regardless of how confident every specialist was — a hard violation is
something the caller could act on or be harmed by, and no confidence score should be able to
override that. Softer findings (tone, phrasing) still just ride along in `qa_score`/`notes`.

**Lesson:** fixing a detection bug and fixing the *use* of that detection are two different
bugs, and testing only the detection (as the previous fix's re-run did) won't surface the
second one — only exercising the full pipeline end-to-end did. Also: a new deterministic check
needs its own false-positive testing against real, messy data before being trusted enough to
gate a queue; "it's not an LLM anymore" is not the same claim as "it's correct."
