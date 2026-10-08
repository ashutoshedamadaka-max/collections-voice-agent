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
