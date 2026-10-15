# Evaluation evidence

## Human benchmark: withdrawn, awaiting v2 review

All nine original human labels are invalid because they were entered randomly across multiple parameters. Every accuracy/agreement figure and false-write/false-hold count derived from them is withdrawn. This is a label-quality finding, not evidence of agent failure or success.

Original `fixtures/eval/labels.json` is preserved locally, excluded from evaluation. `fixtures/eval/benchmark_status.json` records the invalidation. New `label-calls` sessions use `labels_v2.json`; revisions append to a history file. Labels require reviewer identity, timestamp, source hash and evidence for each assessed field. Unknown fields are unscored rather than guessed. Reviewers describe supported records and human follow-up separately. A blind transcript label does not establish whether an unseen proposed record is safe to write, so that score remains unavailable.

`run-eval` rejects altered source transcripts and excludes uncertain/unusable calls. No valid reviewed labels means no score and no API calls. Do not use the model's predictions as replacement ground truth. See [rubric v2](EVAL_LABELING_RUBRIC_V2.md) for the diverse-case sampling and review procedure. The existing nine similar calls alone do not establish representative coverage.

# Eval 2 — the live call, simulated as text (`sim-eval`)

The replacement human eval will grade the **post-call** pipeline on a finished transcript. Almost
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
uv run collections-agent sim-eval --agent-model gpt-4o --max-usd 1.00 # the production model
```

Application budget controls reserve a conservative input allowance and bounded output before each request, including concurrent specialists. SDK retries are disabled for simulations. Missing usage or a harness failure stops the run; unresolved reservations remain visible. Prices are versioned local configuration, not a provider/account billing limit. Verify prices before any paid run. The dry-run estimate is a planning estimate, not the amount reserved per request.

Every new run records source hashes (including uncommitted changes), scenario inputs, models, prices, date and settings in `manifest.json`. Grader v2 checks successful tools of the relevant type and includes the opening in disclosure checks. Claims are regex heuristics and still require adversarial human review. End-to-end text results and conditional post-call diagnostics are reported separately. Harness errors stop on the affected scenario. Historical runs remain unchanged and cannot be compared directly to a new grader without labeling the version change.

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
