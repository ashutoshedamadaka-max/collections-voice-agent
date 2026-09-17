# PRD — B2B Collections Voice Agent

*Status: draft, written alongside Step 0 of the build. Revisit once real call data exists
(Step 4) and again after the eval pass (Step 7).*

## Problem

Collection calls fail from missing context and lost commitments, not from bad talking. A
collector who doesn't know the invoice history, prior promises, or open disputes on a call
either re-asks questions the customer already answered (burning goodwill) or fails to pin a
vague answer down to a specific, trackable commitment. The result: promises get made and
forgotten, and nobody can say with confidence what fraction of promised money actually shows
up on time.

## Users

- **AR analyst / collections lead** — runs the queue, reviews the exception queue, watches
  the headline metric.
- **Finance head** — cares about the promise-to-pay kept rate and the non-payment taxonomy
  rollup (it's a diagnostic for the order-to-cash process, not just a collections score).
- **AP clerk on the other end of the call** — the actual person being called; brief,
  specific, unembarrassed interactions respect their time.

## The agent-vs-workflow decision, and why voice is deterministic here

This is framed as an agent (it holds a phone conversation, branches on what it hears), but
the call itself is deliberately **not** agentic in the open-ended sense: the opening
disclosure, authority gate, opt-out handling, hard duration cap, and the set of things the
agent is permitted to say are all hard-coded outside the model. The LLM's job is phrasing
and branch detection within a fixed set of branches — not deciding what's allowed. Everything
upstream (which accounts to call, in what order, with what facts) and downstream (structured
extraction, promise validation, dispute routing, compliance scoring) is plain code or
narrowly-scoped extraction, not an autonomous agent making judgment calls. The one place
real judgment happens is the post-call supervisor, and even there the answer to
low-confidence or disagreeing analysis is "ask a human," not "guess."

## Success metrics

- **Headline: promise-to-pay kept rate** — of PTPs captured on calls, what share were
  honoured by the promised date. See `docs/metrics_definitions.md` for the precise
  computation once written.
- **Secondary:** contact rate, structured-outcome rate (calls that produced a clean PTP,
  dispute, or documented reason — not "no answer" or an ambiguous outcome), disputes
  surfaced, human-review rate (share of calls the supervisor routed to Exceptions instead of
  auto-writing).
- **Process metric, not vanity:** "calls we correctly did not make" — every suppression is
  logged with a reason; a high, well-reasoned suppression rate is a sign the system is
  targeting the right accounts, not calling everyone.

## Non-goals

- **No automation past 90 days overdue.** Those conversations carry legal and relationship
  risk an automated caller shouldn't take on — always routed to a human.
- **Not a payment collector.** The agent never negotiates discounts, waivers, or settlements,
  and never threatens legal action or credit holds.
- **Not a dispute resolver.** Disputes are logged and routed to the team that can act
  (billing/sales/ops/logistics); the agent never argues the customer's claim.
- **No autonomous write-back.** Anything below the confidence threshold, or where the
  post-call specialists disagree, goes to a human, not into the CRM.
- **No auto-sent email.** Follow-up emails are drafted for human review, never sent directly
  to a customer.

## Open questions to resolve before the eval pass (Step 7)

- What confidence threshold in practice produces a sane human-review rate — 0.75 is the
  starting default (`config/priority_weights.yaml` / `Settings.confidence_threshold`); tune
  once real specialist outputs exist (Step 4).
- Whether the non-payment taxonomy rollup should be a standing weekly artifact even outside
  the eval, since the doc argues this is the most under-appreciated part of the product.
