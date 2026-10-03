# Demo console — UI spec

The public demo front end for the B2B collections voice agent. A recruiter opens it,
talks to the agent, and watches the system reason about the call in real time.

Layout inherits the look of the Clinic Voice Agent Console design canvas: warm ivory
ground, Instrument Serif over Instrument Sans, one amber accent reserved for the moment
the system defers to a human.

---

## 1. The constraint that shapes everything

**Most of the left panel cannot populate during the call.** Only tool calls fire live
(`record_ptp`, `log_dispute`, `send_document`, `schedule_callback`). The five specialists
and the supervisor merge run *after* the call ends, and take a few seconds.

So the demo has three phases, and the UI must make all three feel intentional:

| Phase | Left panel | Centre | Right |
|---|---|---|---|
| **In call** | Account context + captured fields as tools fire | Ripple, live | Transcript growing |
| **Just ended** | Five specialist chips, pending → resolving | Ripple settles to idle | Transcript complete |
| **Settled** | Full analysis + write decision + sheet rows | Call summary + replay | Transcript scrollable |

Do not hide the middle phase. Watching five named checks resolve one by one is the most
legible moment in the whole demo — it is the system showing its work. Give each chip a
visible pending state and resolve them as results arrive, not all at once.

---

## 2. Zones

```
┌──────────────────┬───────────────────┬─────────────────────┐
│  WHAT THE AGENT  │    THE CALL       │   CONVERSATION      │
│    CAPTURED      │                   │                     │
│     ~380px       │     ~440px        │      ~480px         │
│                  │                   │                     │
│  (sheet-bound    │   voice ripple    │  turn-by-turn       │
│   data)          │   + controls      │  + tool annotations │
├──────────────────┴───────────────────┴─────────────────────┤
│  COST & TOKENS — duration · call cost · pipeline cost ·     │
│  total · at 1,000 calls/mo · [gpt-4o-mini | gpt-4o]  ~110px │
└─────────────────────────────────────────────────────────────┘
```

Below 1100px wide, stack: call, then captured, then transcript, with the cost bar fixed
to the bottom.

---

## 3. Left — What the agent captured

Everything here is what does or doesn't reach the Google Sheet. That framing should be
explicit in the panel's subhead: *"This is what gets written to the ledger."*

### 3a. Account context (present from load, before the call)

Company name · contact · primary invoice ID · outstanding amount · days overdue ·
aging bucket as a small pill (1–30 / 31–60 / 61–90).

One line underneath, small: which invoice was deferred and why, since the
one-invoice-per-call rule is a design decision worth surfacing.

### 3b. Captured live (appears as tools fire)

Each is a card that slides in the moment the webhook receives the tool call:

- **Promise to pay** — amount, date, method, invoice. Amber left edge until validated.
- **Dispute** — reason code, the customer's stated detail, evidence requested, routing target.
- **Document requested** — doc type, invoice.
- **Callback scheduled** — reason, priority.

Show the tool name in small monospace on each card (`record_ptp → PTP-59494cb6`). A
recruiter should be able to see that a named function ran, not that text appeared.

### 3c. Analysis (streams in after the call)

Five chips, each pending → resolved, with its confidence:

1. **Outcome** — outcome type, reason code from the taxonomy, next action
2. **Promise validation** — complete? future-dated? within outstanding? method valid?
   If any fail: **downgraded to soft commitment**, with the reason
3. **Dispute** — classified, routed, or "none found"
4. **Compliance** — disclosed automated · authority verified · stayed within permitted
   facts · no discount offered · no consequences threatened · totals arithmetically
   consistent. Any failure is a **hard violation**.
5. **Summary** — two or three sentences

### 3d. The write decision — the loudest element on the page

One card, and the only place amber (`#7A4E12`) is used:

- **`auto_write`** — green. "Written to PTP_Register and Call_Log."
- **`exception_queue`** — amber. "Held for human review." Plus the *reason*, in plain
  words: low confidence, specialists disagree, or a named compliance violation.

Then the actual rows as written, styled as spreadsheet rows with their tab names
(`Call_Log`, `PTP_Register`, `Disputes`, `Exceptions`), and a link to the demo sheet.

**This card is the argument of the entire project.** A promise the system refused to
write, with a stated reason, is a better demo than a clean success. If the call goes
cleanly, offer a "replay a flagged call" button that plays a saved transcript through
the same pipeline so the flagged path is always reachable.

---

## 4. Centre — The call

Vertical stack, generous whitespace. This column is calm; the other two are dense.

- **Who we're calling** — one line, small caps
- **Voice ripple** — concentric rings driven by audio amplitude. Two visually distinct
  states: *agent speaking* (green `#2E5E4E`, outward pulse) and *listening* (slate
  `#3A4A66`, responsive to the mic). Idle is a slow near-still breath, not a flatline.
- **Status line** — "Listening" / "Agent speaking" / "Processing the call…"
- **Controls** — Start call (primary) / End call (secondary). A real `<button>`, with
  `aria-live` on the status line.
- **Mic status** and a recording note: *demo calls are not recorded.*
- **Call timer**, with the 180-second cap shown as a thin depleting bar. The cap is a
  cost control worth making visible.

---

## 5. Right — Conversation

Turn-by-turn, newest at the bottom, auto-scrolling.

- Agent turns: left-aligned, ivory card
- Customer turns: right-aligned, tinted card, with a two-letter avatar
- Timestamps, small
- A language pill at the top (English)

**Between turns, inline annotations** — the best borrowing from the original design. A
thin centred line when something structural happens:

- *Authority confirmed — disclosure complete*
- *Promise captured — ₹2,04,500, 30 Sep, UPI*
- *Discount request declined — routed to team*
- *Callback scheduled for the deferred invoice*

These are what turn a transcript into an explanation.

---

## 6. Bottom — Cost and tokens

One horizontal bar, five figures, all real:

| Figure | Source |
|---|---|
| Duration | Vapi call record |
| Voice call cost | Vapi `cost` field |
| Post-call pipeline cost | Sum of the five specialist calls |
| Total per call | The two added |
| At 1,000 calls / month | Total × 1,000, stated as an extrapolation |

**Right-hand toggle: `gpt-4o-mini` | `gpt-4o`.** Switching it changes every number to the
measured figures from the two control calls:

- gpt-4o-mini: $0.1954 per call, 1.86s mean latency
- gpt-4o: $0.2538 per call, 2.16s mean latency

And one line of consequence, because the money is not the point: *gpt-4o fired both tool
calls and asked the full-or-partial question; gpt-4o-mini narrated one tool call without
invoking it.* That is the tradeoff made visible rather than asserted.

**Label it honestly.** One call each. Put "n=1 per model, same account and script" in
small text under the toggle. An unlabelled benchmark from two calls is the kind of thing
an interviewer catches.

---

## 7. What we do not have, and must not fake

- **Per-turn latency segments.** Vapi gives call-level latency, not a speech-to-text /
  model / text-to-speech breakdown. The original design's 800ms segmented budget bar has
  no data behind it. Either drop it, or check whether Vapi exposes segment timings before
  designing around it.
- **Per-turn token counts.** Pipeline tokens are known per call, not per turn.
- **Recordings.** Demo calls are not recorded, by choice. Say so on the page.
- **A kept rate.** Not enough call volume. It belongs in the written case study with its
  caveats, not on a live console as though it were a running metric.

---

## 8. Visual system

Inherited from the Clinic Voice Agent Console canvas.

**Type:** Instrument Serif (display, headings) over Instrument Sans (body, UI).
Small monospace for tool names, IDs and figures.

**Palette:**

| Token | Hex | Use |
|---|---|---|
| Ground | `#F7F6F2` | page background |
| Surface | `#FFFFFF` | cards |
| Border | `#E4E2DB` / `#EDEBE4` | hairlines, dividers |
| Text | `#57564E` | body |
| Muted | `#6E6C63` | captions, labels |
| Green | `#2E5E4E` | confirmed, auto-written, agent speaking |
| Slate | `#3A4A66` | listening, structural annotations |
| Slate light | `#7FA4EE` / `#9DBDF5` | accents on slate |
| Amber | `#7A4E12` | **flagged / held for review only** |

Amber appears in exactly one place. If it is used for warnings, hovers or emphasis
elsewhere, the exception card stops meaning anything.

**Accessibility:** real `<button>`, `<a href>`, `<label>`; `aria-live="polite"` on the
status line and on newly arriving transcript turns; 4.5:1 text contrast; states
distinguished by lightness as well as hue, not colour alone.

---

## 9. Build notes

- Server-sent events from the FastAPI app for live updates; the page never polls Vapi
  directly.
- Every panel needs an empty state that reads as deliberate, not broken.
- The demo writes to a **separate sheet** from the evaluation data, seeded fresh per
  visitor.
- Hard caps enforced server-side: max duration, calls per visitor, calls per day. Show
  the remaining allowance on the page when it runs low.
- The Vapi public key ships to the browser by design — restrict it to the demo domain
  and the demo assistant in the Vapi dashboard.
