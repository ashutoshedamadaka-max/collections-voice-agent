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

## 2026-09-17 — Call_Log's recording link was dead on arrival, not merely expiring

**Symptom:** every recording link stored in `Call_Log` was unclickable. The working theory
going in was that Vapi's recording URLs are presigned and expire after some days.

**Diagnosis:** checked a real raw payload (`fixtures/raw/*.json`) field by field. The plain
`recordingUrl` (top-level and `artifact.recordingUrl` — what `postcall/writeback.py` was
storing) points at a private Cloudflare R2 bucket (`hipaa-recordings/...`) with no signature —
private buckets reject an unsigned `GetObject` outright, so this link never worked, on any
call, not just "eventually." The actually-working link is `artifact.presignedMonoUrl` — same
object key, with an AWS SigV4 signature query string — and it expires in ~30 minutes
(`X-Amz-Expires=1800`, confirmed against `artifact.presignedUrlsExpiresAt`, which is always
computed ~30 minutes past whenever the call payload was fetched). No stable, storable link
exists anywhere in the payload; no dashboard-call-URL field exists either (checked the full
payload, not just the recording fields).

**Fix:** `postcall/transcript.py::extract_recording_link` picks the presigned variant when
present. `Call_Log.recording_url` no longer stores a URL at all — `writeback.py` writes
`"fetch-recording {call_id}"` instead. The new `fetch-recording <call_id>` CLI command
(`cli.py`) re-fetches the call from Vapi on demand and prints a link that's fresh at the moment
you actually want to listen, instead of one that was already dead (or would be within half an
hour) by the time anyone opened the Sheet.

**Lesson:** "presigned and expires eventually" and "never worked to begin with" look identical
from the outside (both are a dead link in a spreadsheet) but have different fixes — the first
needs a shorter refresh cycle, the second needs re-fetching on demand, always. Only reading the
actual field names and comparing the working vs. non-working URL's object keys side by side
told them apart; guessing from the symptom alone would have "fixed" this by shortening a
refresh window that was never the real problem.

## 2026-09-17 — Vapi's call summary field exists but has never once fired

**Symptom:** wanted to add a call summary to `Call_Log`, cheaply, by using Vapi's own
end-of-call summary if it was already being pulled for free. Checked all four real call
payloads (`fixtures/raw/*.json`): every one has `"summary": ""` and `"analysis": {}`, and
`costBreakdown.analysisCostBreakdown.summary` (and its token counts) are all `0` — not a bad or
empty result, but zero evidence the summary generator was ever invoked.

**Diagnosis:** `call.analysis.summary` is populated by `assistant.analysisPlan.summaryPlan`,
which defaults to `enabled: true` with an out-of-the-box 2-3 sentence prompt — exactly what was
wanted, and this project never explicitly configured it either way. Checked the current Vapi
OpenAPI spec directly: the entire `AnalysisPlan` object (and every plan nested under it,
including `summaryPlan`) is marked `"deprecated": true` on both `CreateAssistantDTO` and
`Assistant`. The zero-cost evidence above is consistent with a deprecated mechanism that no
longer actually runs for a project that never set it, regardless of what its documented default
claims.

**Fix:** rather than explicitly set a deprecated field and hope it revives, added a fifth
post-call specialist (`postcall/specialists/summary.py::extract_summary`) that generates the
same "2-3 sentences: what was said, what was agreed, what happens next" summary from the
transcript this project already pulls, using the same `extract_structured` call-site every
other specialist uses. It's purely descriptive — explicitly excluded from
`_material_confidences` in `pipeline.py`, so a vague summary can never force a call to the
exception queue the way a low-confidence promise or compliance finding does.

**Lesson:** a schema field's documented default (`enabled: true`) describes what the field
would do if honored, not a guarantee that Vapi's backend still honors it — `deprecated: true`
on the *configuration* path is real signal even when the *output* field it feeds
(`Analysis.summary`) isn't itself marked deprecated. Checking real payloads (zero cost spent on
every single call) settled it faster than reasoning from the spec's prose alone would have.

## 2026-09-17 — the first real Hinglish call: five findings, one call

The first live Hinglish test call (`01a0afd5-44e2-7000-859b-01f47f6dc3af`) ran the language
wiring from the same day for the first time against real audio. `endedReason` was
`exceeded-max-duration` (186s against a 180s cap) — not a crash — but the transcript surfaced
five distinct, real problems before that. All five are logged here with evidence from the raw
payload (`fixtures/raw/01a0afd5-...json`), not just from listening back.

**1. Hinglish was implemented wrong — the bot spoke formal Devanagari Hindi, not Hinglish.**
`language.py`'s hinglish prompt instruction never specified a script, only "code-switch
naturally." The model defaulted to full Devanagari, formal register: `"क्या आप बता सकते हैं कि
भुगतान का तरीका क्या होगा?"` — grammatically correct Hindi, but not what a bilingual Indian
speaker actually texts or says, and not what "Hinglish" means (Hindi content in Latin/Roman
script, English loanwords for numbers and business terms, informal register). This also broke
the number/invoice fix from earlier the same day: the `(say "...")` spoken forms
(`voice/speakable.py`) are English words, generated on the assumption they'd sit inside a
Latin-script sentence — embedded in a Devanagari sentence they don't fit grammatically, and the
model didn't use them at all, saying `"₹2,04,000"` and a fragmented `"S। L/26-27/। 0। 0। 03"`
instead. **Fix:** rewrote the hinglish instruction to explicitly require Latin script, English
for all numbers/amounts/invoice IDs/business terms, and an informal register, with a concrete
example (`voice/language.py`).

**2. Transcriber language was wrong for Hinglish.** Set to `"en"` (matching the voice), it was
given genuinely code-switched Hindi/English audio and rendered the customer's side of the call
entirely in **Urdu script** — e.g. `"ہاں، میں دیکھتا ہوں۔ اکاؤنٹس۔"` for what was almost
certainly spoken Hindi/Hinglish ("haan, main dekhta hoon, accounts"). Hindi and Urdu are the
same spoken language (Hindustani) with different scripts; a transcriber mismatched on language
can plausibly guess the wrong one instead of failing loudly. **Fix:** `transcriber_language()`
now maps hinglish to `"hi"`, diverging from `voice_language()` (still `"en"`) for the first
time — the two were the same function before this (`provider_language`), which silently assumed
voice and transcriber should always agree. Not yet re-validated against a real call.

**3. The 180-second cap ran out with two invoices still in play — open, pending a product
decision, not yet changed.** The call fully resolved invoice 1 (amount, date, method, recorded
via `record_ptp`) and was mid-negotiation on invoice 2 (had a vague date, no method) when the
cap hit. Two options on the table: raise `MAX_CALL_DURATION_SECONDS` (e.g. to 240s), or have
the agent commit to resolving one invoice well and explicitly schedule a callback for the rest
rather than racing the clock across all of them. See the CLI/chat response for the recommendation
and reasoning; not implemented until confirmed.

**4. A genuine barge-in stutter loop, confirmed from raw message timing, not guessed.** The bot
said `"ठीक है, मैं।"` ("Okay, I—"), cut off after 1.14s, then said the *identical* fragment
again 1.5s later, also cut short (0.66s). Cross-referencing `secondsFromStart`/`duration` on
every message: the customer's utterance at message 8 (`"ہاں، مل گیا۔"`) started at 53.22s —
*inside* the bot's message 7 window (52.352s–53.49s). Same pattern for the second cutoff. This
is real interruption/barge-in, not a text-generation glitch. Checked Vapi's `StopSpeakingPlan`
schema: at the default `numWords: 0`, ~0.2s of any detected customer voice activity interrupts
the assistant regardless of what was said, bypassing `acknowledgementPhrases` (the list of
backchannel words that never interrupt) — and that list is English-only ("okay", "got it",
"yeah"), so Hindi backchannels ("haan", "theek hai", "mil gaya") had no protection at all.
**Fix:** `assistant_config.py` now sets a `stopSpeakingPlan` with `numWords: 3` (so the
threshold and phrase lists actually apply instead of raw voice-activity timing) and an expanded
`acknowledgementPhrases` list adding Hindi/Hinglish backchannels alongside Vapi's English
defaults. This is assistant-level, not per-call — takes effect only after `create-assistant` is
re-run, and hasn't been re-validated against a real call yet.

**5. Redundant reason question.** The customer volunteered `"payment is under approval"`
(`"پیمنٹ ابھی اپروول میں ہے"`) in response to the initial promise question — already a
complete answer, matching `awaiting_internal_approval` in the reason-code taxonomy — before the
agent ever asked why. The agent asked "what's the reason for this delay" anyway. **Fix:** added
a general CONVERSATION RULE ("if the customer already gave a piece of information... even
unprompted... never ask again") rather than a narrow fix to just the reason field, since the
same failure mode (mechanically running a fixed question script instead of tracking what's
already been said) could recur for amount/date/method too; updated the "Cannot pay now" branch
to reference it instead of unconditionally asking why.

**Lesson:** four of five findings here were root-caused from data already in the raw payload
(message-level timestamps, `assistantOverrides`, the OpenAPI spec for the interruption
settings) rather than from re-listening to the call or guessing at plausible causes — the same
discipline as the recording-link and call-summary findings earlier the same day. The one
finding that IS a guess pending validation (fix 2, transcriber language) and the one still
open by design (fix 3, the call cap) are marked as such rather than presented with the same
confidence as the other three.

## 2026-09-17 — running the untested path: five specialists against a Hindi/Urdu-script transcript

Deliberately ran `run-postcall` + `write-back` on the flawed Hinglish call above, specifically
*because* the language caveat logged earlier the same day ("the four post-call specialists...
assume English transcripts... untested") had never actually been exercised. Results:

**What worked, better than expected.** `extract_outcome` (`promise_to_pay`, 0.95) and
`extract_promise` — which pulled `amount: 204000`, `date: 2026-09-30`, `method: UPI`,
`invoice_ids: ["SL/26-27/0002"]` all correctly, confidence 0.9 — extracted clean structured
facts from a transcript where the customer's every line is Urdu-script and the bot's lines are
Devanagari. `extract_summary` produced an accurate, fully English 2-3 sentence summary
covering both invoices correctly. `classify_dispute` reasonably read the 10%-reduction ask as a
`pricing_dispute` on the right invoice (`SL/26-27/0003`). None of this was obviously degraded
by the language mismatch — the specialists' English-tuned prompts still made sense of
Hindi-content English-loanword text well enough to extract facts correctly.

**What failed: `review_compliance` produced a false positive, and its own free-text
contradicted its own boolean.** It set `promised_discount_or_waiver: true` for a turn where the
agent said `"यह संभव नहीं है, मैं इसे टीम को बताऊँगा"` ("this isn't possible, I'll tell the
team") — an explicit refusal that routes the request, exactly matching this same day's
discount-refusal fix. Its own `notes` field even describes this correctly — *"the agent did
imply it was **unable** to provide the discount... stating it would relay the request"* — and
then still flagged the hard-violation boolean anyway, reasoning that relaying the request
"affects negotiations." Whether this is specific to reasoning about a Hindi-language exchange
or a latent ambiguity in the compliance rubric that an English call would also trip is not
established — this transcript doesn't isolate the variable.

**The safety net worked regardless of the specialist's mistake.** All four gating specialists'
individual confidences were high (0.95 / 0.9 / 0.85 / 0.9) and `overall_confidence` (0.85)
would have cleared this project's confidence threshold comfortably — this call would have
auto-written on confidence alone. It didn't, because `_hard_compliance_violations` treats
`promised_discount_or_waiver` as an unconditional override regardless of confidence (the
product decision from the 2026-09-16 arithmetic-bugs entry: "no confidence score should be
able to override that"). The call correctly landed in `Exceptions` for a human to review — for
the wrong specialist-level reason, but the right final outcome. Call_Log still got a full row
(duration, cost, the generated summary, the `fetch-recording` pointer) since that write is
unconditional regardless of `write_decision`.

**Lesson:** "the untested path is broken" and "the untested path is safe" can both be true at
once. The compliance specialist's judgment on this transcript should not be trusted — that
much is confirmed. But the supervisor architecture built in Step 4 (hard violations override
confidence unconditionally) is exactly what kept a wrong specialist judgment from becoming a
wrong auto-written call. This is one data point, not a validated Hindi pipeline — worth another
real call, ideally one without a compliance-relevant event, to see whether extraction quality
holds without a hard violation there to mask whether confidence calibration itself is still
trustworthy on non-English transcripts.

## 2026-09-17 — one-invoice-per-call redesign, and the compliance false positive fixed

Two follow-ups to the Hinglish call findings above, once the product decision on the 180s cap
was made.

**Decision on the call-duration cap: don't raise it, redesign the call instead.** Raising
`MAX_CALL_DURATION_SECONDS` (e.g. to 240s) only relocates the failure — any fixed cap runs out
eventually against a chattier customer or a third invoice, and a chunk of tonight's overrun was
the barge-in loop and bad transcription (both fixed above), not inherent conversation length.
Instead: **the agent now works exactly one invoice — the most overdue unpaid one — to a
complete outcome per call, and schedules a callback for any others**, rather than walking every
invoice in sequence. This also matches actual collections practice better: a clean resolution
plus a booked callback beats several rushed, vague commitments squeezed into one call.
`MAX_CALL_DURATION_SECONDS` stays at 180 — if the redesign works, the cap should stop being hit
at all, which is itself the check that it worked.

**Implementation:** `ContextPack` gains `primary_invoice_id` (`precall/context_pack.py`'s new
`_worst_invoice` — the same "most overdue, ties broken by larger outstanding" selection that
already drove the account's aging bucket, reused rather than inventing a second notion of
priority). `prompt_template.py` renders the primary invoice separately from any others, and the
FACTS section explicitly tells the model not to negotiate, ask about, or read out details for
the others — just acknowledge them and route to a callback. GOAL is now scoped to "the primary
invoice only," and CLOSING adds a step to call `schedule_callback` (naming the deferred
invoices) before ending, when there are any. Not yet validated against a real multi-invoice
call — the next Hinglish (or any multi-invoice account) test call should confirm the agent
actually stops after the primary invoice instead of continuing on its own.

**Compliance false positive, fixed.** The rubric line — *"did it offer, hint at, or negotiate
any discount or waiver"* — never distinguished the agent proposing a discount from the topic
merely coming up; the specialist conflated "a discount was discussed" with "a discount was
granted," flagging a clean refusal as a violation. The fix adds an explicit distinction
(`promised_discount_or_waiver` means the agent *proposed, suggested, or agreed to* reduce or
waive an amount — a refusal is compliant regardless of phrasing or language) plus two worked
examples the model can pattern-match against: the actual refusal line from the flawed call in
English ("that's not something I can adjust, I'll flag it to the team") and in the Hindi it was
actually spoken in ("यह संभव नहीं है, मैं इसे टीम को बताऊँगा"), both explicitly marked
compliant. Re-ran `run-postcall` on the same call afterward to confirm — see below.

**The framing that matters here, and why it's being written down explicitly:** the hard-
violation override exists to stop a *confident wrong auto-write* — a call where every
specialist feels sure of itself but something specific and dangerous happened anyway (a
discount actually offered, a threat actually made). Here, that same override caught a
*confidently wrong violation* instead: nothing dangerous happened, but the mechanism built to
catch danger regardless of confidence did its job on a false alarm just as unconditionally as
it would have on a real one. That's the system behaving exactly as designed — but a compliance
rubric that can't tell "a discount was discussed" from "a discount was granted" will produce
this exact false positive on every future call where a customer merely asks. A queue that's
full of calls where nothing was actually wrong is a queue nobody reads carefully by the third
one — the identical lesson already learned from the arithmetic-consistency regex's own false
positive on garbled digit-by-digit transcription (2026-09-16, "two follow-on bugs found by
re-running the fix above"). A deterministic check and an LLM rubric can both cry wolf; both
need their own false-positive testing before being trusted enough to gate a queue, and finding
out via a real call is exactly what happened here.

## 2026-09-17 — stale rows across re-analysis, fixed with an audit trail, not deletion

Fixing the compliance false positive above and re-running `run-postcall` on the same call
exposed the gap `writeback.py` had already flagged as a known limitation: the corrected
analysis (`auto_write`) needed to write a `PTP_Register` row and a `Disputes` row, but the
*first* analysis's `Exceptions` row (`compliance_violation`, now wrong) was still sitting there
with nothing marking it superseded. Two rows, same call_id, contradicting each other, with
nothing in the Sheet distinguishing "this was the answer" from "this used to be the answer."

**Options considered:** (1) a status/timestamp marker on the stale row, or (2) have write-back
delete rows in tabs the new decision doesn't target. Went with (1). `SheetsBackend` has no
delete verb at all — building one (Google Sheets' API deletes by row *index*, not by key,
which is a meaningfully different and riskier operation than the upsert-by-key writes this
project has done everywhere so far) would be new surface area to get right just to make the
sheet look clean. An audit trail matters more than a clean sheet in a collections system
anyway: "this call was flagged, then a re-analysis cleared it" is exactly the kind of thing
someone reviewing the account later needs to see, not something worth erasing.

**Scope turned out to be bigger than the one tab that surfaced the bug.** The same staleness
can hit `PTP_Register` (a promise re-analyzed into a soft commitment, or into nothing), a
brand-new `Soft_Commitments` (a soft commitment re-analyzed into a complete promise), and
`Disputes` (a dispute that a re-analysis no longer finds) — not just `Exceptions`. Fixed all
four the same way, reusing scaffolding that mostly already existed and was simply never wired
up: `PTPStatus.SUPERSEDED` was already a defined enum value nothing ever set;
`ExceptionEntry.resolved` was already a free-text field a human was expected to fill in by
hand — an automated `"resolved_by_reanalysis (<timestamp>)"` note fits the same field without a
new column. Only `Disputes` (`DisputeStatus.SUPERSEDED`, new enum value — `RESOLVED` already
means something else: a human handled it) and `Soft_Commitments` (`superseded: bool`, a new
field — it had no status concept at all before this) needed anything new.

**Mechanism** (`postcall/writeback.py`'s `_reconcile_stale_rows`, called at the start of every
`write_back`): compute what the *current* analysis says should exist in each of the four
tabs for this `call_id`, and for whichever ones it says should *not* exist, check for a
previous row (same deterministic id: `PTP-{call_id}`, `SC-{call_id}`, `DSP-{call_id}`, or
`Exceptions` keyed by `call_id` directly) and mark it superseded/resolved — but only if it
isn't already marked, so a human's own manual resolution note is never overwritten. Re-running
the *same* decision twice remains a true no-op, exactly as before.

**Lesson:** a "known limitation" noted in a docstring is still a real bug waiting for the
re-analysis that triggers it — this one had been sitting there since Step 5, wasn't
hypothetical, and surfaced the first time a genuine specialist mistake was actually fixed and
re-run. Documenting a limitation is not the same as it being acceptable to leave open once
there's a concrete instance of it in front of you.

## 2026-09-17 — merged the opening; found a second prompt regression nothing tested for

Two findings from the next validation call, before the actual four-fix validation call could
even happen.

**1. The disclosure and authority question were two separate turns, with a dead pause between
them.** `firstMessage` spoke only the disclosure; the model then generated its own authority
question as its first real turn, after waiting for whatever filler the caller said in the gap
("Um, Boli?" on the earlier Hinglish call, itself burned turns on before anything substantive
happened). Merged both into one deterministic `firstMessage` — `voice/language.py`'s
`opening_message()`, hand-written per language like `_PROMPT_INSTRUCTION`, not model-generated,
since Vapi speaks `firstMessage` before the model runs at all. `assistant_config.py`'s
`build_assistant_payload` gets a generic (no contact name) fallback; `build_call_overrides` now
also overrides `firstMessage` per call with the personalized version — confirmed via the
OpenAPI spec that `AssistantOverrides.firstMessage` exists, so this didn't require inventing
anything. The prompt's `# AUTHORITY GATE` section changed from "ask this before stating an
amount" to "this was already asked in the opening, do not ask again — read their answer."

This is a small architectural improvement, not just a pacing fix: disclosure and authority are
the two things in this whole design that must never vary — never reworded, never skipped,
never (for authority) asked twice. Putting both in a fixed `firstMessage` makes both
structurally guaranteed rather than dependent on the model remembering to say them, the same
way moving arithmetic out of the model (2026-09-16 entries) made *that* guaranteed instead of
hoped-for.

**2. The agent never asked how much the caller would pay** — it stated the invoice's own
outstanding total as a fact, then asked only for a date and a method, and recorded the full
total as the promised amount without the caller ever confirming it. In B2B collections a
partial payment is completely normal; "I'll pay by the 30th" is genuinely ambiguous between
paying everything and paying part, and the agent silently resolved that ambiguity in the
direction that makes the recorded promise look better than what was actually agreed.

**Diagnosis, honestly qualified:** checked whether the one-invoice-per-call redesign or the
bundled-questions fix had literally dropped "amount" from the required-fields text — neither
had; both still listed "an amount, a date, and a method" verbatim. What most likely changed is
emphasis, not text: the primary-invoice redesign put the invoice's own outstanding total
front and center as *the* fact for "the one thing to resolve this call," which plausibly gave
the model an available number to silently adopt as the payment amount instead of treating it as
context to ask against. This can't be confirmed without another live call — no test caught the
original bug either, for the same underlying reason described below.

**Fix:** rewrote the "Will pay" branch to ask date, then an explicit full-or-partial amount
question (with a follow-up only if partial), then method — never inferring the amount from the
invoice's own stated total. Also added a line to GOAL making the same point explicitly: "an
amount (full or partial — ask explicitly, never assume the full outstanding balance)."

**The real gap: no test checked prompt *content* for what the agent is actually instructed to
ask.** 249 (then 254) passing tests didn't catch either this or the bundled-questions bug
earlier the same day, because every prompt test up to that point checked *facts* (does the
right invoice number appear) or isolated *phrases* (does "one question per turn" appear
somewhere), never "does the Will-pay branch actually instruct asking for amount, date, *and*
method, each as its own explicit ask." A prompt is code that produces behavior exactly like any
other code path, and a refactor can silently delete a requirement from a paragraph of English
as easily as from a line of Python — with no import error, no type error, nothing but a live
call to catch it. Added `test_will_pay_branch_asks_each_required_promise_field_explicitly`
(`tests/test_prompt_template.py`) asserting all three fields are explicitly asked for, so a
future refactor that drops one fails a test instead of waiting for another live call.
