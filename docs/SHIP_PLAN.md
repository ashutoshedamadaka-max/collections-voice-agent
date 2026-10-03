# Ship plan

Single source of truth for what's left before this goes on the portfolio. Supersedes anything
said about remaining work in chat.

**Steps 1–3 (label the calls, place the verification call, write the README and record the
video) are independently publishable.** None of them depend on the live demo being deployed
anywhere. The deployed live demo (step 5) is additive — a stronger version of the portfolio
piece, not a gate on shipping it. If you want to ship this week, steps 1–3 are the whole
critical path.

Every item below was checked against the repo on 2026-10-03, not assumed. Where your framing
in the request was slightly off from what's actually in the code, that's called out explicitly
in the item, not smoothed over.

---

## Corrections to your framing, up front

Three places where what's actually in the repo differs from "built but just needs the last
step":

1. **None of the demo console, eval, or Pass 3 work is committed.** `git status` shows Pass
   1/2/3 (`demo.html`, `demo_replay.py`, `demo_live.py`, `static/`), the eval infra
   (`eval/labels.py`, `eval/compare.py`, `docs/eval.md`), and the README/FAILURES.md updates
   all sitting as uncommitted changes or untracked files. Right now, none of this exists in
   the repo's history — only on this disk.
2. **The demo writes nothing to any Sheet, ever — currently.** Checked both `demo_live.py`
   and `demo_replay.py` for any call to `write_back`/`write_ptps`/`write_call_log`/
   `SheetsBackend`: zero. The write-decision card shows a *preview* of what would be written;
   nothing actually is. What the demo *does* do today is **read** real account/invoice rows
   (`ACC-0019`, `ACC-0001`) out of your one existing `GOOGLE_SHEET_ID` — the same sheet the
   eval and real pipeline runs use. So "separate demo sheet" isn't only a write-contamination
   problem (there's no write path yet to contaminate anything) — it's also a read-coupling
   problem: the public demo currently depends on, and exposes account names/contacts from,
   your live operational sheet.
3. **The spoken-form fix has never been tested on a real call — not "the last test happened
   to be before the fix," literally impossible for it to have been.** The fix
   (`23f0e07`, "Structural spoken-form fix") was committed 2026-09-30 11:02 IST. The last real
   call activity anywhere in the repo (`fixtures/webhook_requests.jsonl`,
   `fixtures/tool_calls.jsonl`) is 2026-09-29 ~21:21 IST — nine hours *before* the fix existed.
   The garbled date in that last call ("2026 September. 13.") is exactly the failure that
   *motivated* the fix, not a test of it. Zero real calls have happened since Pass 3's wiring
   (`demo_live.py`, created 2026-09-30 20:13) was even written.

---

## 0. Commit and push the current work

**Verified state:** Uncommitted/untracked, as above. This is the one item not in your list —
everything else in this plan is unreachable by anyone looking at the GitHub repo until this
happens.

**Done when:** `git status` is clean (or only shows expected gitignored artifacts) and the
remote reflects it.

**Effort:** 10 minutes. Review the diff once for anything that shouldn't be committed (check
`.env` isn't in it — it's gitignored, but double-check nothing else sensitive crept into
`fixtures/webhook_requests.jsonl` or similar before adding it).

**Blocks:** Everything. A portfolio link to this repo currently shows none of Pass 1–3 or the
eval work.

---

## 1. Eval — label the nine calls, run the report

**Verified state:** Matches your understanding exactly. `label-calls` and `run-eval` both
exist and work (I ran both end-to-end against test data while building them). `fixtures/eval/
labels.json` does not exist — zero calls labeled. `fixtures/eval/pipeline_runs/` is empty.

**Done when:** `uv run collections-agent label-calls` reports all 9 labeled, then
`uv run collections-agent run-eval` produces the agreement table and disagreement printout
without error.

**Effort:** Free (no API cost to label). ~30–45 minutes of your own time reading nine
transcripts and judging them, splittable across sittings (it's resumable — that was the point).
`run-eval` itself costs a few cents in OpenAI credit (9 calls × 5 specialists, gpt-4o-mini) and
takes a couple of minutes, hands-off.

**Blocks:** The eval agreement number, if that's what you want in the README (see item 3 — this
is genuinely ambiguous right now and worth deciding before writing that section).

---

## 2. Pass 3 — the live verification call

**Verified state:** Matches your understanding. The wiring (`demo_live.py`, the SSE live
channel, the Vapi Web SDK integration in `demo.html`) is built and was smoke-tested (config
endpoint, tunnel reachability, assistant registration) but **no live call has ever gone through
it.** Separately and additionally: this same call is the only way to verify the spoken-form fix
on a real call, since none has happened since the fix landed.

Two things to actually check on this call, not one:
- **The demo page itself:** does the left panel populate from the webhook in real time, does
  the ripple/transcript render correctly from the Vapi Web SDK's own events, does the real
  pipeline run and stream specialist results after hangup.
- **The spoken forms** (the actual point of placing this call per your last message): does the
  agent speak the `(say "...")` forms for the date, amount, and invoice ID — not raw digits.
  The date is the one that broke before (gpt-4o said "2026 September 13" for 2026-09-30) and
  is worth listening for specifically.

**Before dialing:** the tunnel almost certainly needs restarting and the assistant's
`server.url` re-pointed — every prior session in this project has found the tunnel dead after
any gap, and it's been four days. Budget time for that, not just the call itself.

**Done when:** one real call placed, the demo page showed it correctly end to end (transcript,
tool-call cards, specialist resolution, final write decision), and the transcript shows the
agent speaking the invoice number/amount/date in spoken form, not digits.

**Effort:** 30–45 minutes — most of it re-establishing the tunnel and assistant pointer
(pattern established repeatedly this project), not the call itself (~3 minutes) or reading the
resulting transcript.

**Blocks:** Confidence that live mode works at all. Does **not** block publishing — you can
ship with Pass 1/2 (static shell + replay mode) demonstrated and Pass 3 marked "built,
verification pending" if you choose not to spend the time now.

---

## 3. Portfolio — README, video, write-up

**Verified state:** None of this exists. No video file in the repo, no portfolio/case-study
language in README beyond the architectural "headline metric: promise-to-pay kept rate" line
(which is a description of what the metric *would* be in production, not a reported number —
there's no production volume for a kept rate to mean anything, and `docs/eval.md` already says
so explicitly). No Lovable write-up found anywhere.

**One decision this plan surfaces rather than makes:** you said "README with the number" —
singular — without saying which number. Two real candidates exist and they are not
interchangeable:
- The **eval agreement result** (item 1) — "the pipeline agreed with my own judgement on X of 9
  calls, per field" — honest, caveated, but depends on item 1 being done first.
- The **measured cost figures** already in hand from the two control calls
  ($0.1954/gpt-4o-mini vs $0.2538/gpt-4o per call, n=1 each) — already real, already
  documented, no dependency on anything else.

Pick one (or use both — they answer different questions: one's about correctness, one's about
cost). Don't let "the number" stay undefined until you're writing the README.

**Done when:**
- README has a short, honest results section with whichever number(s) you pick, explicitly
  scoped (n=9, mostly synthetic; n=1 per model) — no language that could be read as a
  production claim.
- A ~90-second video exists (screen recording is enough) showing the demo console in at least
  one mode — replay mode doesn't require item 2 to be done; a live call does.
- Lovable write-up drafted, linking to whichever of the demo/video is ready.

**Effort:** README section, 30–60 minutes once the number is picked. Video, 1–2 hours
(recording plus minimal editing/trimming — budget more if you want narration). Lovable
write-up, 30–60 minutes.

**Blocks:** Nothing downstream — this is the last step before publishing, not a dependency for
anything else.

---

## 4. Deployment — not started, additive

Nothing here blocks shipping steps 0–3. Start this only once you've decided you want a live,
clickable demo link rather than a video, and treat it as its own project, not an afternoon.

**Verified state for each piece:**

| Piece | What's actually true now |
|---|---|
| Host | No deployment config of any kind (`Dockerfile`, `Procfile`, `render.yaml`, `fly.toml` — none exist). Your instinct to rule out Vercel is correct and verifiable from the code, not just a hunch: `demo_live.py`'s live SSE channel is a **module-level, in-process `asyncio.Queue`** — it requires one persistent process, not a per-request serverless function. Render/Railway/Fly (anything that runs a long-lived container) works; Vercel-style serverless does not, structurally. |
| Hard caps (duration, per-visitor, per-day) | Not built. `demo_live.py`'s own docstring says so directly: "single-caller, local-testing scope only... would need revisiting before any multi-visitor deployment." No rate-limiting code anywhere in `webhooks/`. |
| Separate demo sheet | Not built — and per the correction above, this is now a two-part gap: no write path exists yet at all (nothing to isolate), and the read path currently points at your one real `GOOGLE_SHEET_ID`. |
| "Never recorded" claim | Literally on the page (`demo.html` line ~833: "Demo calls are never recorded or stored as audio.") with nothing backing it — no `recordingEnabled` (or equivalent) field set anywhere in `assistant_config.py`. Flagged, not fixed, when this was built, specifically because guessing an unverified Vapi field name has silently no-opped before in this project (`docs/FAILURES.md`). |
| Vapi public key restriction | Can't be verified from the repo at all — this is a Vapi Dashboard setting (API Keys → restrict by domain + assistant), not code. Check it there directly. |
| SQLite vs. replace | No SQLite anywhere in the codebase currently (checked `pyproject.toml` and all of `src/`). This is a forward decision tied to however hard caps (above) end up storing visitor/day counters, not an existing-code question — resolve it as part of designing that storage, not standalone. |

**Done when (per piece):**
- Host: app reachable at a stable (non-tunnel) URL, SSE streaming confirmed working through it.
- Hard caps: a call past the duration cap is actually cut off; a visitor past their call
  allowance sees a blocked state, not a started one; this is true after a server restart too
  (which is where the SQLite-or-not decision actually bites).
- Demo sheet: demo reads (and, if write-back ever gets wired in, writes) hit a sheet that is
  not your eval/production sheet, seeded with synthetic data.
- Recording claim: either the claim is made true (a verified Vapi field, tested against a real
  call's resulting record) or the copy is changed to not claim it. Either resolution is fine;
  leaving it as an unverified claim on a public page is the thing to not ship.
- Vapi key: dashboard shows the key restricted to your demo domain and assistant specifically.
- SQLite: a one-line decision recorded somewhere (this file is fine) — "counters live in
  [SQLite on a persistent volume / Postgres / Redis], because [host]'s disk
  [persists/doesn't] across restarts."

**Effort (rough, each is its own sitting, not a unit):**
- Host + first deploy: 1–3 hours, mostly platform-specific friction (env vars, build config),
  not code.
- Hard caps: 2–4 hours (design the storage, write the checks, test the restart case).
- Demo sheet separation: 1–2 hours (new sheet, adapt the seed script, settings wiring).
- Recording claim: 15 minutes if you just change the copy; 30–60 minutes if you chase down and
  verify a real `recordingEnabled`-equivalent field.
- Vapi key restriction: 15 minutes, dashboard only.

**Blocks:** A live, public demo link. Nothing else.

---

## Critical path, in order

```
0. Commit & push ──────────────────────────────────────┐
                                                          ├─→ visible on GitHub
1. Label 9 calls → run-eval (free, any time)            │
2. Live verification call (spoken-form check)            ├─→ publishable portfolio piece
3. README (pick "the number") + video + write-up         │   (steps 1–3, in any order,
                                                          ┘   independent of each other)

── optional, additive, start only if you want a live link ──
4a. Recording-claim decision + Vapi key restriction (cheap, do these first if you proceed)
4b. Hard caps + SQLite/storage decision
4c. Separate demo sheet
4d. Host + deploy
```

**You can ship without:** everything in section 4. Also without item 2, if you're willing to
label Pass 3 "built, pending verification" in the write-up rather than claim it works — not
recommended given it's the one piece that's never been run, but it's not a hard blocker on
publishing the rest.
