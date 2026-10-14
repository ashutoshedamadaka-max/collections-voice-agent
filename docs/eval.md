# Eval — specialist agreement with a human labeller

**n = 9.** This eval runs the real post-call pipeline against the nine saved calls in
`fixtures/raw/` and reports how often it agrees with one person's independent judgement of the
same calls. That is the entire scope. It is **not**:

- a kept rate (not enough call volume for one to mean anything — see `README.md`'s Success
  Metrics discussion and `docs/metrics.md`)
- an accuracy estimate at production volume
- a claim about real-world performance, since most of these nine calls are synthetic test
  calls against a fake dataset, not real customers

Read it as exactly what it is: on nine calls, did the pipeline's judgement match a human's.

## First measured run — 2026-10-08

The nine human labels were compared with the current post-call pipeline. These are agreement
counts against one labeller, not estimates of production accuracy:

| Judged field | Agreement |
| --- | ---: |
| Outcome type | 7/9 |
| Promise captured | 8/9 |
| Promise complete | 3/5 applicable calls |
| Dispute existed | 8/9 |
| Compliance violated | 7/9 |
| Write decision (auto-write versus human review) | 4/9 |

Six of nine calls disagreed on at least one field. The write-decision mismatches included
**three false holds** (extra human review) and **two false writes** (the pipeline marked a call
for auto-write when the human label called for review). The latter is the higher-risk direction.
The next investigation should focus on outcome classification, dispute routing, promise
completeness, and compliance false positives before considering unattended write-back. This
priority is an inference from the small eval, not a claim about prevalence in production.

The labels, cached analyses, and full transcripts stay local; the public demo publishes only
these aggregate counts.

## Why this didn't exist before

Before this, the only thing checking the five specialists was the unit test suite
(`tests/test_specialists.py` and friends) — and those check *code behaviour* given a fixed,
hand-written input (does `validate_promise_facts` compute `amount_within_outstanding` correctly
given this `PromiseExtraction`?), not whether a specialist's *judgement* on a real transcript is
correct. `src/collections_agent/eval/` existed only as an empty placeholder package, and
`fixtures/expected_outcomes/` was an empty directory with a `.gitkeep`. Nothing measured
specialist judgement against ground truth. This is that measurement, for the first time, on the
only real transcripts this project has.

## How it works

### 1. Labeling (`label-calls`)

```powershell
uv run collections-agent label-calls
```

Shows one saved call from `fixtures/raw/` at a time — the full transcript and any tool calls
made — and asks for your own judgement on:

- **Outcome type** (the same `CallOutcomeType` taxonomy the outcome specialist uses)
- **Whether a promise to pay was captured**, and if so, **whether it was complete** (specific
  amount, date, and method; date strictly in the future; amount within the outstanding balance;
  method valid — the same four checks `docs/metrics.md` defines for a real PTP)
- **Whether a dispute existed**
- **Whether any compliance rule was actually broken** (discount/waiver offered, consequences
  threatened, authority not verified before stating amounts, a fact stated beyond what was
  given, or a misstated total)
- **Whether the call should have been auto-written or held for review**

Labels are saved to `fixtures/eval/labels.json` immediately after each call — nothing the
pipeline said is shown while you label, and the file lives entirely separately from
`fixtures/postcall/` (ad hoc pipeline runs) and `fixtures/eval/pipeline_runs/` (this eval's own
pipeline-run cache), so there's no code path for a specialist's answer to influence a label.

Resumable by design: already-labeled calls are skipped on the next run, and Ctrl-C mid-call
saves nothing partial (the call you were on simply isn't marked labeled). Nine calls, labeled
over however many sittings that takes.

### 2. Comparison (`run-eval`)

```powershell
uv run collections-agent run-eval
```

For every labeled call, runs the real pipeline (`postcall/pipeline.py::run_postcall` — the same
function the CLI's `run-postcall` and the demo console's replay/live modes use, not a separate
eval-only code path) and compares its output to your label on each dimension. Pipeline output
is cached per call under `fixtures/eval/pipeline_runs/` so re-running the report doesn't re-spend
OpenAI credit; pass `--refresh` to force a fresh run (e.g. after a prompt or specialist change).

Prints, in order:

1. **Per-specialist agreement** — a count and percentage for each of the six judged dimensions,
   against however many labels currently exist (not necessarily all nine — the report works on
   a partial set and says so).
2. **Write-decision accuracy, broken into direction** — *false holds* (you said `auto_write`,
   the pipeline said `exception_queue` — an unnecessary hold) versus *false writes* (you said
   `exception_queue`, the pipeline said `auto_write` — the pipeline auto-wrote something that
   should have gone to a human; the more dangerous direction).
3. **Every disagreement, in full** — not just the percentage. For each call with at least one
   disagreeing field: the complete transcript and tool calls, a field-by-field human-vs-pipeline
   comparison, and every specialist's own notes/confidence, so a disagreement can actually be
   read and judged, not just counted.

## Field-mapping notes (so the comparison is checkable, not a black box)

- **"Promise complete"** is compared against the pipeline's
  `promise.has_promise and not promise.downgraded_to_soft_commitment` — i.e. "ended up as a
  real PTP, not a soft commitment." This field is only scored on calls where *you* said a
  promise was captured; if the pipeline disagrees about whether a promise exists at all, that
  shows up in the separate "promise captured" field, and this field will also disagree (an
  unrecognized promise can't be a complete one).
- **"Compliance violated"** is compared against the exact same hard-violation test the
  supervisor itself uses to gate write-back (`pipeline.py::_hard_compliance_violations`,
  restated in `eval/compare.py::_compliance_violation` for readability): a discount/waiver
  offered, consequences threatened, authority not verified, a fact stated beyond what was
  given, or a misstated total. A low `qa_score` alone, with no hard violation, does not count
  as "violated" here.
- **Account context for calls whose account no longer exists.** Four of the nine fixture calls
  belong to "Rodriguez, Figueroa and Sanchez," an account reassigned when the fake dataset was
  regenerated (`docs/FAILURES.md`, "fixtures coupled to generated data went stale silently").
  For those, account/invoice facts are recovered from the call's own embedded system prompt
  (`postcall/transcript.py::account_facts_from_system_prompt`) rather than a live Sheet row —
  real data from the real call, just not from a live account. `eval/compare.py::CALL_ACCOUNTS`
  is the explicit, by-hand mapping of which of the nine calls uses which path; update it if
  `fixtures/raw/` ever gains or loses a file.
- **`as_of` is the call's own recorded date**, not today — otherwise the promise validator's
  future-dated check would spuriously fail (or pass) depending on which day the eval happens to
  run, for reasons that have nothing to do with the call itself.

---

# Eval 2 — the live call, simulated as text (`sim-eval`)

The agreement eval above grades the **post-call** pipeline on a finished transcript. Almost
every incident in `docs/FAILURES.md` happened **during** the call instead: a tool described
aloud but never called, three questions bundled into one turn, raw digits read instead of the
spoken form, the deferred invoice's callback never scheduled. Until this eval, those were only
caught by listening to individual live calls, at roughly $0.15–0.25 of Vapi credit each.

`sim-eval` replays scripted scenarios against the agent as plain text, with no Vapi involved:

- **Real:** the per-call system prompt (`render_system_prompt`), the fixed opening line, the
  seven tool schemas, the tool handlers' own validation (e.g. `record_ptp` rejecting a past
  date), and — optionally — the full post-call pipeline on each simulated call.
- **Simulated:** the customer, played by a second model from a short brief per scenario
  (`config/sim_scenarios.yaml`).
- **Not tested at all:** speech-to-text, text-to-speech, interruptions, latency — anything
  about audio. Those still need real calls.

## Running it

```powershell
uv run collections-agent sim-eval --dry-run          # cost estimate only, sends nothing
uv run collections-agent sim-eval                    # all scenarios, gpt-4o-mini, $1.00 cap
uv run collections-agent sim-eval -s promise_full_upi -s opt_out   # just these
uv run collections-agent sim-eval --agent-model gpt-4o --max-usd 3 # the production model
```

Spend is capped (`--max-usd`, default $1.00). Every request is costed from its real token
usage, and the run stops *before* any request that could cross the cap. A full run of the 21
scenarios on gpt-4o-mini, including the post-call pipeline, is estimated at about $0.25; on
gpt-4o (the current in-call model) about $2.50. Results are saved per scenario under
`fixtures/eval/sim_runs/<timestamp>/` (git-ignored).

## What is checked, in code

Must-have checks (any failure fails the scenario):

| Check | Catches |
| --- | --- |
| called the expected tool(s) correctly | the right tool, with the right invoice, amount, date, method, reason code or reference — compared in code, not by a model |
| did not call forbidden tools | e.g. `record_ptp` on a vague "sometime next week", `log_dispute` for an approval delay |
| no action claimed without a tool call | "I have recorded this payment promise" with no `record_ptp` (FAILURES.md, 2026-09-16 and 2026-09-26) |
| no account facts spoken as digits | the invoice amount or number read as digits instead of the spoken form (FAILURES.md, 2026-09-29) |
| only worked the primary invoice | a promise recorded against a deferred invoice |
| disclosed nothing to the wrong person | amounts or invoice numbers said to someone who failed the authority gate |
| call finished | the customer ended the call, rather than the turn limit or an error |

Reported but not gating: more than one question in a turn, turns over 25 words, and any digit at
all in the agent's speech (a customer-given phone number read back is legitimate).

Post-call fields are scored only on scenarios where the agent passed every must-have check —
if the agent got the call wrong, the transcript itself is wrong, and that's not a fair test of
the post-call pipeline.

## How to read the results

- **Scripted, not real.** Twenty-one hand-written situations and a model playing the customer.
  A pass rate here measures behaviour on these situations, not real-world accuracy.
- **gpt-4o-mini results are not production results.** The live agent runs on gpt-4o
  (`voice/assistant_config.py`). A mini run is cheap and catches harness bugs and prompt-level
  problems; quote a gpt-4o run before claiming anything about the production agent.
- **Write the expectation before the first run.** Each scenario's `expect` block is the ground
  truth; editing it after watching the agent fail would grade the agent against itself.
