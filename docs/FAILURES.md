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

## 2026-09-16 — numbers are the agent's weakest speech moment (fixed 2026-09-17)

**Symptom:** Across multiple test calls, amounts and invoice numbers consistently come out
mumbled or hard to follow — e.g. "8 5 0 0. 0 0 0 0 0" for an amount, "Camminis 3000 hundred
81" and "Quinus, 3281" for the same invoice number in one call. Speech quality elsewhere in
the same calls (regular sentences) has been clear. This is bad for a collections agent
specifically, since amounts and invoice numbers are the entire point of the call — a caller
who can't parse what they owe or which invoice is in dispute can't act on the call at all.

**Diagnosis:** never root-caused at the audio level (no way to distinguish TTS mishandling raw
numerals from a testing-connection artifact from the outside) — instead fixed at the only layer
actually controllable: what text reaches the voice provider in the first place. Checked Vapi's
current OpenAPI spec directly: neither SSML (`enableSsml`/`enableSsmlParsing`) nor a
pronunciation dictionary (`VapiVoice.pronunciationDictionary`) is available for the voice
configured here (`provider: "vapi"`, `voiceId: "Naina"`) — both exist only for
ElevenLabs/Cartesia/WellSaid voices, which this project doesn't use. So candidate fix 2 (below,
as originally logged) is a dead end without switching voice providers; candidate fix 1 is the
only one actually available.

**Fix:** `voice/speakable.py` (new) converts every amount to Indian-numbering words
(`amount_to_words`, e.g. `204000.0 -> "two lakh four thousand rupees"`) and every invoice
number to character-by-character spoken form (`invoice_number_to_words`, generalizing the
letter-by-letter convention below to the real generator's `"KFC/26-27/0026"` shape, not just
toy IDs like "KA-3281"). `prompt_template.py` renders both the raw value and a `(say "...")`
spoken form for every invoice, `total_outstanding`, and prior-promise amount; the model is told
to use the spoken form verbatim rather than transform digits itself. The raw form is kept
alongside it deliberately — `postcall/specialists/promise.py::_matches_invoice` and
`record_ptp`'s tool arguments need the literal invoice number/amount, and reconstructing a
number from its word form is exactly the kind of LLM arithmetic this project has already
decided not to trust (see the 2026-09-16 arithmetic-delegation entry above).

**Original candidate fixes, for reference:**
1. Pre-format numbers into words in the context pack, before they ever reach the prompt, so
   the voice layer never sees raw numerals — e.g. "four lakh fifty thousand rupees" instead of
   "450000", and "K-A three two eight one" instead of "KA-3281". Prompt-level instructions
   (letter-by-letter reading, added earlier today) only constrain how the *model* phrases
   things; they don't control how the *voice provider* renders whatever text it's given. **→
   this is the fix that shipped.**
2. Check whether Vapi's voice config exposes SSML or a pronunciation dictionary for the
   current voice/provider (Naina, `vapi` provider) — if so, that may be a more direct fix than
   reformatting text. **→ checked; not available for this voice provider.**
3. Check whether reading two invoices back-to-back produces one long unbroken run of figures
   (per the transcript above, all figures for both invoices land in a single sentence) —
   splitting that into one sentence per invoice may help independently of numeral formatting.
   **→ not investigated this pass; Vapi's `ChunkPlan.punctuationBoundaries` (found while
   checking SSML support) is a possible lever if this resurfaces.**

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

## 2026-09-17 — a Step 1 bug that took until Step 6 to have data to expose it

**Symptom:** None visible — this was caught by re-reading the code while planning Step 6, not
by any test failing or any call behaving wrong. `priority.py`'s `_ptp_reliability_penalty`
computed a broken-promise ratio and used it to *lower* an account's priority score:
```python
penalty = 1.0 - weights.ptp_reliability_broken_penalty * broken_ratio
return max(weights.ptp_reliability_min, min(1.0, penalty))
```
More broken promises → a smaller multiplier → a lower final score → called *less* urgently.

**Why nobody noticed:** `priority_score()` has taken `ptp_history` as a parameter since Step 1
and `tests/test_priority.py::test_broken_promises_lower_priority` explicitly asserted this
exact (backwards) behavior — so it was tested and passing the whole time. But nothing in the
codebase ever supplied real `ptp_history` until Step 6: `test-call`'s CLI command has always
passed `ptp_history=[]` with a comment saying real history "doesn't exist yet," and Step 6 is
the first thing that ever writes a `PTP.status` of `BROKEN` anywhere. A bug in a formula that
never receives real input is invisible by construction — the moment Step 6 made `BROKEN`
statuses real, this would have started actively working against the design doc's stated intent
("broken promises re-queue at an escalated posture") the first time anyone looked at a queue
ordered by this score.

**Root cause:** the formula and its config fields (`ptp_reliability_penalty`,
`ptp_reliability_min`) were named and shaped around treating unreliability as a reason to
*discount* an account, rather than the design doc's actual framing — a broken promise is
evidence the debt needs *more* attention, not less. The bug wasn't a typo or a sign flip in
otherwise-correct logic; the whole mental model encoded in the names was inverted.

**Fix:** renamed and re-derived from scratch rather than patched: `_ptp_reliability_penalty` →
`_ptp_broken_escalation`, `ptp_reliability_penalty`/`ptp_reliability_min` →
`ptp_broken_escalation_weight`/`ptp_reliability_max`. The multiplier is now `1.0 +
weight * broken_ratio`, capped at `ptp_reliability_max` (default 2.0) — always >= 1.0, so a
clean or promise-free history is never discounted, only a real broken-promise track record
gets boosted. `test_broken_promises_lower_priority` was renamed to
`test_broken_promises_escalate_priority` and its assertion inverted, plus a new test locking in
the cap behavior.

**Lesson:** a formula can be fully tested and still be wrong if the tests only exercise it with
the same never-real input the production code path also always passed it. "This is tested" and
"this has ever run against real data" are different claims — the second one is what actually
validates a formula's *direction*, not just its arithmetic. Worth deliberately auditing any
other function that has taken a "not wired up yet" parameter since early steps, once later
steps start actually populating it.

## 2026-09-17 — fixtures coupled to generated data went stale silently

**Symptom:** A postcall fixture built earlier in the project (`FIXTURE-future-promise`)
referenced invoice IDs `KA-3281` and `WI-7558`. When exercising the write-back path against the
real Sheet again for Step 6 testing, both IDs turned out not to exist in `Invoices` anymore —
`validate_promise_facts` couldn't match them to any real outstanding balance, and the fixture's
promise silently downgraded to a soft commitment instead of exercising the path it was built to
test.

**Root cause:** the fake India dataset (`Invoices`/`Accounts` in the Sheet) was regenerated at
some point after this fixture was authored, with a fresh set of invoice IDs. The fixture file
itself has no dependency on the generator and no way to detect that the IDs it hardcodes have
drifted out from under it — it just quietly stops matching real data and produces a different
(wrong) code path than intended, with no error anywhere.

**Fix:** built a fixture *variant* referencing current, real invoice IDs (`INV-00026`,
`INV-00051`) rather than editing the original — consistent with this project's existing
"fixture variant, not edit to original" convention, so the original stays available as
historical evidence of what was tested when.

**Lesson:** any fixture that hardcodes IDs from a separately-generated dataset is fragile by
construction — it is coupled to that dataset's *current* state, not pinned to a snapshot of it,
so regenerating the data invalidates the fixture with no warning. This will bite again at Step 7
(and beyond) every time the fake dataset is regenerated; the durable fix would be either pinning
fixtures to a versioned data snapshot or having fixtures reference accounts/invoices by a stable
role ("the account with an open PTP") resolved against whatever data is live, rather than by
literal ID. Not fixed here — flagged so it isn't rediscovered from scratch next time.

## 2026-09-17 — a new Sheet column, computed correctly, silently discarded on write

**Symptom:** `promise_to_pay_kept_rate_by_value` was added to `MetricsRow` and `TAB_SCHEMAS`
for Step 6. Unit tests passed (200/200 against the in-memory fake backend). Running
`followthrough` for real against the live Sheet — to demo the exact divergence this metric
exists for (a kept ₹46,000 promise and a broken ₹462,000 one) — showed the right numbers in the
CLI's own echo (`kept_rate=0.50 kept_rate_by_value=0.09`), but reading the `Metrics` tab back
directly showed only 8 columns. The 9th field was gone — not blank, not miscomputed, just
absent, with no error anywhere in the run.

**Root cause:** `GspreadSheetsBackend.ensure_worksheet` only ever wrote a tab's header row in
two cases — the worksheet didn't exist yet, or it existed with an empty first row. It never
handled the third case: a header row that already exists but is missing a column a newer
`TAB_SCHEMAS` entry added. `upsert_rows` then builds each row strictly from `ws.row_values(1)`
— the sheet's *actual* header row, not the code's schema — so `str(row.get(h, ""))` iterates
only over the old 8 headers and the 9th field is dropped before a single API call is made.
Compounding it: `ensure_worksheet` (and thus this whole path) is only ever invoked from
`seed-sheet`, a one-time setup command — no write command re-validates a tab's headers, so a
schema change to an *existing* tab had no path to ever reach a live sheet that predated it.

**Why the test suite didn't catch it:** `InMemorySheetsBackend.upsert_rows` (the fake backend
`tests/conftest.py` gives every unit test) stored whatever fields were in the row dict handed
to it — it never projected rows through a stored header list the way the real backend does
through `ws.row_values(1)`. The fake and the real backend diverged on exactly the behavior that
broke, so 200 passing tests against the fake proved nothing about this path.

**Fix:**
1. `ensure_worksheet` now diffs the live header row against the requested headers and appends
   any missing column names to the end of row 1 (`sheets/client.py`) — real schema evolution,
   not just first-time creation.
2. `upsert_models` (`sheets/writers.py`) now calls `backend.ensure_worksheet(tab, headers)`
   itself before every write, not just once via `seed-sheet` — every write path self-heals its
   own tab's headers instead of depending on someone remembering to re-run setup after a schema
   change.
3. `InMemorySheetsBackend` (`tests/conftest.py`) rewritten to actually mirror this: it now
   backfills missing headers the same way, and `upsert_rows` projects every row through the
   tab's stored header list before storing it — so the fake can no longer accept a field the
   real backend would silently drop. New tests (`tests/test_sheets_writers.py`) simulate a
   stale pre-existing header row and assert the missing column gets backfilled and populated.

**Lesson:** a fake backend that is *more permissive* than the real one is worse than no fake at
all for the behavior where they differ — it makes every test pass while proving nothing about
that behavior. The tell here was the same one from the 2026-09-16 arithmetic bugs: "the tests
pass" and "this ran correctly against the real system" are different claims, and the second one
only got checked because this session's plan required actually reading the live Sheet back, not
just trusting the CLI's own echo of numbers it computed in memory.

## 2026-09-17 — language support wired in, but only the call itself is validated

**What changed:** `account.preferred_language` (already generated into the fake dataset as
`en`/`hi`/`hinglish`, but never read anywhere) now flows from the `ContextPack` into the Vapi
voice config (`version: "latest"` + `language`), the Soniox transcriber's `language`, and a
`# LANGUAGE` instruction in the system prompt (`voice/language.py`, wired into
`assistant_config.build_call_overrides` and `prompt_template.render_system_prompt`). Hinglish
gets English voice/transcriber settings — neither provider has a dedicated Hinglish code — with
a prompt instruction to code-switch naturally instead. Unset or unrecognized values default to
English. Deliberately **not** built: an in-call language question. Asking wastes the opening
seconds and reads as an IVR menu; language is a pre-call property of the account, decided
before the call starts, not something negotiated during it.

**What this does not validate:** every downstream piece of this pipeline — the four post-call
specialists (`postcall/specialists/*.py`), the reason-code taxonomy, the arithmetic-consistency
extraction (`check_arithmetic_consistency`, regex-based over the transcript's own phrasing) —
was built and tested exclusively against English transcripts. A Hindi call produces a Hindi
transcript, and nothing in that path has ever been run against one: the dispute/promise/
compliance prompts, the regex patterns, and the reason-code strings are all English-shaped.
Wiring the voice/transcriber/prompt language is necessary but not sufficient for a validated
Hindi pipeline — it is a demonstrated capability on one test call, not a tested pipeline.
**English stays the default for evaluation and the demo.** Any real Hindi (or Hinglish) call
should be treated as exploratory until the postcall path is deliberately tested against a real
Hindi transcript.

**Backlog, deliberately not built:** detecting the customer's actual spoken language mid-call
and writing it back to `Account.preferred_language` so the next call opens in the right
language. Nothing here does that; every call still opens in whatever language was set the last
time a human (or the fake data generator) set it.

## 2026-09-17 — two prompt defects discussed after a call, never written down

Both of the following were noticed on real test-call transcripts and talked through at the
time, but neither was logged — they only resurfaced from memory when auditing this file before
a prompt-fix pass, and had to be re-confirmed against transcript evidence after the fact.
**Lesson, ahead of the two bugs themselves:** an observation about a call that isn't written
into this file doesn't survive past the conversation it was made in — the audit step that
almost missed these two is now the reason to write incidents down the same day, not "once
there's time."

**1. Bundled questions despite an explicit one-question-per-turn rule.** On the first
promise-to-pay call, the agent asked "what amount, when, and how" as a single turn instead of
three; the caller's answer came back incomplete twice as a result. `system_prompt.j2` already
has "One question per turn. Then stop." (CONVERSATION RULES) — so this is the model ignoring an
existing rule, not a missing one. Root cause traced to two other places in the same prompt that
contradict it with more specific, more proximate phrasing:
- GOAL section: *"Ask once for the specific date and amount."* — literally instructs asking for
  two facts in one question.
- BRANCHES section: *"Will pay → pin down amount + date + method, confirm back, call
  record_ptp."* — a compact three-item conjunction with no turn-by-turn structure, sitting in
  the table the model consults at the exact moment it decides how to respond.

Both read as an immediate to-do list for one turn, and both are more specific and closer to the
point of decision than the generic rule stated once, several sections earlier. The same
compact-conjunction pattern also appeared in three other BRANCHES lines ("ask for the reference
number and date," "ask what specifically and what they need from us," "ask when they expect to,
and why, once") — same latent bug, not yet independently observed on a call for those branches.

**Fix (2026-09-17):** rewrote every BRANCHES line using this pattern into explicit sequential
asks (e.g. "Will pay → Ask for the amount, the date, and the method as three separate
questions — never combine them.") and reworded the GOAL section's "Ask once for the specific
date and amount" into per-piece retry language that cross-references the existing
one-question-per-turn rule instead of restating it. No new rule was added.

**2. Correct policy, robotic delivery on a discount refusal.** Verbatim from a dispute call,
after the caller asked for a discount:
> "I cannot negotiate discounts or waivers. Will you pay the full outstanding amount on any
> invoice? If yes, please specify amount, date, and method."

The refusal itself was correct — no discount was offered or hinted at. Two things are wrong
with how it was said: it echoes the internal rule's own wording back to the caller ("negotiate
discounts or waivers" closely paraphrases HARD PROHIBITIONS' "Never offer, hint at, or
negotiate a discount, waiver, or settlement") instead of speaking like a person, and the
follow-up sentence is bug #1 again ("specify amount, date, and method" bundled into one ask). A
real collector declines and redirects in one natural sentence — something like "that's not
something I can adjust — the dispute goes to the team and they'll come back to you" — without
naming the policy.

**Fix (2026-09-17):** added one BRANCHES entry ("Asked for a discount or waiver → Decline in
one natural sentence without naming the policy or using words like 'negotiate' or 'policy' —
e.g. \"That's not something I can adjust — I'll flag it to the team and they'll get back to
you.\"") to `system_prompt.j2`. HARD PROHIBITIONS' wording is unchanged — the prohibition stays
absolute; this only gives the model a natural line to say instead of reciting the rule.
