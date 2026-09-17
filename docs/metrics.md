# Metric definitions

This is the reference for what every number in the `Metrics` tab means, and why it's computed
that way — not a comment buried in `metrics/rollup.py`. If a number in that tab needs
defending to a reviewer, the argument lives here.

## What counts as a promise vs. a soft commitment

A **promise to pay** (`PTP_Register`) requires all three of: a specific amount, a specific
date, and a payment method — and the date must be strictly in the future, and the amount must
be within the invoice(s)' actual outstanding balance, and the method must be one of the
accepted payment rails (NEFT, RTGS, UPI, cheque, portal). All four checks are done in code
(`postcall/specialists/promise.py:validate_promise_facts`), not asked of the LLM — an earlier
version asked the model to also judge completeness and arithmetic, and it got a check wrong on
a case where the correct answer was knowable in advance (`docs/FAILURES.md`).

If any of those checks fail — "soon," a vague date, an amount that doesn't match any invoice,
a promise that's already in the past — it's downgraded to a **soft commitment**
(`Soft_Commitments`) instead: a note that the caller intends to pay, without the specificity
that would let the system hold them to a date. Soft commitments are never checked by the
follow-through job — there's no date to check them against.

## What counts as contact

**`contact_rate`** = calls where a person actually engaged, divided by calls made:
```
count(outcome != no_answer) / calls_made
```
A hostile pickup or a wrong-person pickup still counts as contact — a human answered the
phone. That's deliberately broader than the next metric.

**`structured_outcome_rate`** = calls that produced a usable collections outcome:
```
count(outcome not in {no_answer, hostile, wrong_person}) / calls_made
```
Narrower on purpose. A wrong-person call produces a reroute, not a collections decision, and a
hostile call produced contact but no actionable outcome. Separating these two rates is what
lets you tell "we're reaching people" apart from "reaching people is working" — collapsing
them into one number would hide exactly the distinction a reviewer would ask about.

## The headline number: promise-to-pay kept rate

```
promise_to_pay_kept_rate = count(status == KEPT) / count(status in {KEPT, PARTIAL, BROKEN})
```

**Only promises that have actually come due and been checked count at all.** A promise still
`OPEN` — not yet due, or due but still inside its grace period — is excluded from both the
numerator and the denominator. It hasn't been judged yet; counting it either way would either
inflate the rate with unproven promises or deflate it by treating "not yet resolved" as
"failed." `SUPERSEDED` (a promise replaced by a newer one) is excluded the same way, though
nothing in the codebase sets that status today — excluded defensively, not because it's been
observed.

**Why `PARTIAL` counts in the denominator but never the numerator:** a partial payment is a
real, resolved outcome — it belongs in "promises that came due and were judged" — but it is
not a *kept* promise. Two ways to get this wrong, both worse than the current definition:
- Fractionally crediting it (e.g. 50% paid = 0.5 kept) makes the metric a measure of money
  collected, not promises kept, and blurs into the rupee-weighted metric below for no reason.
- Excluding it from the denominator entirely (kept / (kept + broken)) makes the rate look
  better than it should — an agent whose callers habitually pay half and vanish would show a
  clean kept-rate with this definition, which is the opposite of what the number is for.

**Why cumulative, not a daily cohort or a trailing window:** at this project's real call
volume (the design doc's own test-call list is ~12-15 calls total), a same-day cohort is
almost always `0/0` or `1/1` — not a number worth putting in front of a reviewer. Cumulative
(recomputed as a fresh snapshot every run, not a rolling delta) is stable from day one and is
the version worth defending. A trailing 30/60/90-day window is the right production evolution
once there's enough volume for recency to matter more than raw stability — a one-line change
to the query, not a redesign — but it isn't worth the extra date-arithmetic surface before
then.

### The rupee-weighted variant

```
promise_to_pay_kept_rate_by_value =
    sum(amount_promised for KEPT) / sum(amount_promised for KEPT + PARTIAL + BROKEN)
```

Same numerator/denominator logic, weighted by the amount actually promised instead of the
promise count. This exists because the count-based rate alone can be misleading: a ₹5,000
promise kept and a ₹5,00,000 promise broken both count as "one kept, one broken" — a 50% rate
that hides that 99% of the money at stake didn't come in. The rupee-weighted number answers
"how much of what people promised actually arrived," which is closer to what the business
actually cares about than "what fraction of promises, regardless of size."

Neither number replaces the other — a healthy count-based rate with a weak value-weighted one
means small promises are being kept and large ones aren't (or vice versa), which is itself a
useful signal the single-number version can't show.

## The grace period

`DEFAULT_GRACE_PERIOD_DAYS = 3` (`followthrough/job.py`). A promise isn't checked — not marked
kept, partial, or broken — until 3 days after its promised date have passed. Bank transfers
(NEFT/RTGS especially) can take a day or two to clear and be reflected wherever payments get
recorded; checking on the promised date itself would mark a promise `BROKEN` that was actually
paid on time and is just still clearing.

The check is all-or-nothing at the grace deadline, not "resolve early if the money's already
in": a promise paid in full a day after the promised date still isn't marked `KEPT` until day
3. This trades a small amount of reporting lag for having exactly one code path decide the
outcome, instead of two different ones (paid-early vs. paid-during-grace) that would need to
be separately reasoned about, tested, and explained here.

3 days is a starting assumption, not a measured one — there's no real payment feed yet to
calibrate against (see below). Worth revisiting once real settlement times are observed.

## The payments source

There is no real payment-gateway or bank integration. The `Payments` tab is filled in by a
human today — someone reconciling bank statements enters what actually got paid. The
follow-through job depends on a `PaymentsSource` Protocol (`followthrough/payments.py`,
mirroring `sheets/client.py`'s `SheetsBackend` Protocol exactly), not on "the Payments tab"
directly, so a real integration can implement the same one-method interface later without any
change to the matching/checking logic in `followthrough/job.py`.

This is a different, more trusted signal than the in-call `log_payment_claim` tool
(`webhooks/handlers.py`): a customer saying "I already paid" mid-call is an unverified claim,
logged only for a human to follow up on — it is never written to the `Payments` ledger and
never resolves a promise on its own.

## `calls_suppressed` — a known, honest gap

Hardcoded to `0`. Nothing writes to the `Suppressed` tab yet, because there is no daily
dispatch/`build-queue` job that would produce suppression decisions to log — the only calling
path today is the manual `test-call` command. Reporting `0` here is a documented placeholder,
not a claim that zero calls were suppressed.
