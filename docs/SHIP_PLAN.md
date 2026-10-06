# Ship plan

Single source of truth for what's left before this goes on the portfolio. Supersedes anything
said about remaining work in chat.

**Steps 1–3 (label the calls, place the verification call, write the README and record the
video) are independently publishable.** None of them depend on the live demo being deployed
anywhere. The deployed live demo (step 4) is additive — a stronger version of the portfolio
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

## 0. Commit and push the current work — DONE

**Landed:** commit `54fa829` ("Add demo console (Pass 1-3), eval harness, and two pipeline bug
fixes" — 24 files, +4005/-32) pushed to `origin/master` at
[github.com/ashutoshedamadaka-max/collections-voice-agent](https://github.com/ashutoshedamadaka-max/collections-voice-agent),
15 commits, public. Before pushing: scanned the *full* commit history (not just the latest
diff) for secrets — every filename ever committed, every historical diff's content against
API-key/PEM-key patterns, and the exact current values of every secret in `.env` substring-
matched against the whole log. Nothing found. `.gitignore` excluded `.env` and the
service-account path from commit #1, not added partway through — `.env` itself has zero
history; the one hit (the Vapi assistant ID, inside the committed `fixtures/raw/*.json` call
records) is a non-sensitive identifier, not a credential.

**Done when:** ~~a remote is set and `git push` reflects this commit on GitHub~~ — done, 15/15
commits present on `origin/master`, working tree clean.

**Blocks:** Was blocking everything; now blocks nothing. **Note for item 4 (Deployment):** the
remote is also a prerequisite there, separately — most hosts (Render/Railway/Fly included)
deploy by connecting to the GitHub repo directly, so this had to exist before that step could
start regardless of portfolio visibility.

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

**Blocks:** One row of the README's results table (item 3 — the number decision is resolved:
both numbers appear, so this one's needed regardless, not optionally).

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

**Resolved decision (2026-10-03): neither number leads.** Both candidates invite exactly the
scrutiny this project has otherwise been careful to avoid — the cost comparison is n=1 per
model, the eval agreement will be n=9 with anchoring risk either way (a strong-looking
percentage reads as more rigorous than nine calls can support; a weak-looking one reads as a
confession). Leading the README with either turns it into the headline a reviewer pulls on
first, instead of the design decisions that are the actual point.

**README structure instead:** lead with what the system does and the design decisions behind
it (confidence-gated write-back, the structural spoken-form fix, the supervisor's
disagreement/compliance-override logic, what replay mode caught that no unit test could). Only
after that, a results table carrying **both** numbers side by side, each with its `n` stated
directly in the table, not in a footnote:

| | Result | n |
|---|---|---|
| Specialist agreement with human labels | (from `run-eval`, once labeled) | 9, mostly synthetic |
| In-call model cost | $0.1954 (gpt-4o-mini) vs $0.2538 (gpt-4o) per call | 1 per model |

Numbers as evidence for the design discussion, not as the headline.

**Done when:**
- README leads with system description + design decisions; the results table (both numbers,
  both `n`s stated inline) appears after that, not before it; no language anywhere that could
  be read as a production or headline claim.
- A ~90-second video exists (screen recording is enough) showing the demo console in at least
  one mode — replay mode doesn't require item 2 to be done; a live call does.
- Lovable write-up drafted, linking to whichever of the demo/video is ready.

**Effort:** README section, 30–60 minutes (structure is decided; writing it is what's left).
Video, 1–2 hours (recording plus minimal editing/trimming — budget more if you want
narration). Lovable write-up, 30–60 minutes.

**Blocks:** Nothing downstream — this is the last step before publishing, not a dependency for
anything else.

---

## 4. Deployment — not started, additive

Nothing here blocks shipping steps 0–3. Start this only once you've decided you want a live,
clickable demo link rather than a video, and treat it as its own project, not an afternoon.

**The GitHub remote (item 0) is a prerequisite here too, separately from portfolio visibility**
— Render/Railway/Fly all deploy by connecting directly to a GitHub repo, so this step couldn't
have started without it regardless. Already satisfied.

**Verified state for each piece:**

| Piece | What's actually true now |
|---|---|
| Host | **DONE — targeting Render.** `render.yaml` added: native Python runtime (uses the repo's existing `uv.lock`/`.python-version`, no Dockerfile), free plan by default, build `uv sync --frozen`, start `uv run uvicorn collections_agent.webhooks.server:app --host 0.0.0.0 --port $PORT`. Your instinct to rule out Vercel was correct and verified from the code, not just a hunch: `demo_live.py`'s live SSE channel is a module-level, in-process `asyncio.Queue` — it requires one persistent process, not a per-request serverless function. See "Deploying to Render" below for the exact dashboard walkthrough, every env var and where its value comes from, and what the free tier's cold-start behavior means for a recruiter clicking in. |
| Hard caps (duration, per-visitor, per-day, spend floor) | **DONE.** `webhooks/demo_caps.py` (SQLite, stdlib) tracks per-visitor-window count, daily count, and cumulative spend; `demo_live.py`'s `/demo/live/config` and `/demo/live/status` check all three (spend floor → daily ceiling → visitor window, in that order) before ever handing the browser a working `publicKey`/`assistantOverrides`. All five numbers configurable via `.env` (`DEMO_BUDGET_USD`, `DEMO_SPEND_FLOOR_USD`, `DEMO_DAILY_CEILING`, `DEMO_VISITOR_WINDOW_HOURS`, `DEMO_CALLS_PER_VISITOR_WINDOW`). Verified live against the real server: seeded the daily ceiling and spend floor directly via `demo_caps` functions and confirmed `/demo/live/config` correctly blocks with the right `reason`, and the frontend falls into `startReplay('clean')` with an explanatory line instead of attempting `vapi.start()` — never a dead button. Duration cap (180s) was already handled via Vapi's own `maxDurationSeconds`, unchanged. **Known, accepted limitation:** no operator bypass (a capped-out day just shows replay, which is the designed-acceptable degradation) and the live SSE channel is still single-caller (concurrent visitors could interleave) — both deliberate, see the implementation plan for why. |
| Separate demo sheet | **Partially resolved.** The *exposure* half — the part that was true the instant the page was reachable, with zero write-back even wired in — is fixed: `webhooks/demo_fixtures.py` inlines the real account/invoice values as a frozen fixture; both public demo paths (`demo_live.py`, `demo_replay.py`'s clean scenario) now call zero Sheets APIs at runtime, verified by pointing `GOOGLE_SHEET_ID` at garbage and confirming both still work. The *contamination* half is moot for now, same as before — write-back still isn't wired into either public demo path, so there's still nothing to contaminate. If that changes later, a real separate demo sheet is still the right answer; not needed today. |
| "Never recorded" claim | **DONE — verified, not guessed.** `artifactPlan.recordingEnabled: false` confirmed against docs.vapi.ai/assistants/call-recording (works in both the base assistant config and per-call `assistantOverrides`; defaults `true` otherwise) and set in both, in `voice/assistant_config.py`. Covered by two new tests. **Operator action still needed:** re-run `create-assistant` against whatever URL the deployed/tunneled server is on — the code change alone doesn't update the already-registered live assistant. |
| Vapi public key restriction | **Verified dashboard steps, documented below** — not a code change. See "Vapi public key restriction" at the end of this item. |
| SQLite vs. replace | **Resolved, verified against Render's own docs (docs.render.com/free, docs.render.com/disks): stays SQLite, on the free plan it does not persist.** `demo_caps.py` uses stdlib `sqlite3` against a configurable file path (`DEMO_STATE_DB_PATH`). Render's **free** web services cannot attach a persistent disk at all — any local filesystem write, SQLite included, is wiped on every restart, redeploy, *and* spin-down (free services spin down after 15 minutes idle). Practical effect: the spend floor and daily ceiling reset every time the demo wakes from idle, not just on a real redeploy. The fix is a **paid Starter plan ($7/mo) with a persistent disk attached (~$0.25/GB/mo — 1GB is overkill for this file)**, mounted at a path you then point `DEMO_STATE_DB_PATH` at. Decision for you: free is fine to ship with (caps still work correctly *within* a boot, which covers the realistic case of one recruiter clicking in at a time) as long as you accept that a cap count can reset after idle gaps; upgrade later if that bothers you. Not swapping SQLite for Postgres/Redis now — same underlying tradeoff, more moving parts, no benefit at this scale. |

**Done when (per piece):**
- Host: app reachable at a stable (non-tunnel) URL, SSE streaming confirmed working through it,
  and `DEMO_STATE_DB_PATH` confirmed to survive a restart on that host (see the SQLite row).
- Hard caps: ✅ done — see table row above.
- Demo sheet: ✅ exposure half done — see table row above. Revisit the contamination half only
  if/when write-back gets wired into a public demo path.
- Recording claim: ✅ done — see table row above. Remember to re-run `create-assistant` once
  deployed.
- Vapi key: dashboard shows the key restricted to your demo domain and assistant specifically
  (steps below).
- SQLite: ✅ decision made — see table row above; the remaining "done when" is host-specific
  (persistent volume mounted and tested, not just assumed).

**Vapi public key restriction — exact dashboard steps** (verified against
docs.vapi.ai/security-and-privacy/api-keys, not guessed):
1. Dashboard → **API Keys** → **Public API Keys**.
2. Select your existing public key (or **Add Key** for a dedicated demo-only one).
3. **Allowed Origins** — add the deployed domain as a complete URL, no trailing slash (e.g.
   `https://your-demo-domain.com`).
4. **Allowed Assistants** — select the demo assistant specifically from the dropdown, not
   "all assistants."
5. Save. A request from any other origin, or for any other assistant, is rejected by Vapi
   before it reaches your server — this is enforcement Vapi does, not something this repo's
   code can do on its own, which is exactly why the public key being shipped to the browser
   (by design, same as `test-call --dial` already does) is safe once restricted this way.

**Deploying to Render — dashboard walkthrough.** `render.yaml` (committed) makes this a
Blueprint deploy, not a field-by-field manual setup:

1. **Render Dashboard → New → Blueprint.** Connect your GitHub account if you haven't, then
   select `collections-voice-agent`. Render reads `render.yaml` from the repo root
   automatically and shows you the one service it defines before creating anything.
2. **You'll be prompted for each secret env var** (`sync: false` in the blueprint means
   "ask the human," not "leave blank"): `VAPI_SERVER_SECRET`, `VAPI_PUBLIC_KEY`,
   `VAPI_ASSISTANT_ID`, `OPENAI_API_KEY`, and optionally `VAPI_OPENAI_CREDENTIAL_ID`. Where
   each value comes from:

   | Variable | Where to get it |
   |---|---|
   | `VAPI_SERVER_SECRET` | Copy verbatim from your local `.env` — it's the credential Vapi already has attached to your assistant's server URL (`webhooks/auth.py`'s docstring: "the secret configured in the dashboard, attached to the server URL as a credential"). It must match what's already registered there, so don't invent a new one. |
   | `VAPI_PUBLIC_KEY` | Copy verbatim from your local `.env` (Vapi Dashboard → API Keys, if you ever need to re-find it). |
   | `VAPI_ASSISTANT_ID` | Copy verbatim from your local `.env`. |
   | `OPENAI_API_KEY` | Copy verbatim from your local `.env`. |
   | `VAPI_OPENAI_CREDENTIAL_ID` | Copy from local `.env` if you ever set one (BYOK billing); leave the prompt blank if not — it's optional. |

   Everything else (`COMPANY_NAME`, `CONFIDENCE_THRESHOLD`, the five `DEMO_*` cap settings)
   is pre-filled in the blueprint with this project's current values — nothing to type, and
   each is editable later from the service's **Environment** tab without a code change.
3. **Create Web Service.** First build takes a few minutes (watch the build log — `uv sync
   --frozen` should report the same "Checked 61 packages" style output it does locally).
4. **You get a URL** of the form `https://collections-voice-agent-demo.onrender.com` (or
   whatever Render assigns/you customize). That's the deployed URL for everything below.
5. *(Optional, only if you want caps to survive idle restarts — see the SQLite row above)*:
   Dashboard → your service → **Settings** → change **Instance Type** from Free to
   **Starter** ($7/mo), then **Disks** → add one (name `data`, mount path e.g.
   `/var/data`, 1GB is plenty) → update `DEMO_STATE_DB_PATH` in **Environment** to
   `/var/data/demo_state.db` → save (this redeploys).

**What the free tier's cold start means for a recruiter clicking in** — verified against
Render's own docs, not assumed: a free web service spins down after **15 minutes with no
inbound traffic**, and the *next* request takes about **one minute** to spin back up. If a
recruiter is the first visitor in a while, `/demo` itself will hang for up to a minute before
anything renders — not a cap being hit, not an error, just the container waking up. Worth
deciding whether that's acceptable for a job-search link (most will wait; some will bounce)
or worth the $7/mo Starter plan, which has no spin-down. The two decisions (cold start,
SQLite persistence) point at the same fix, so it's really one call: free (accept both
tradeoffs) or Starter (fix both at once).

**Effort remaining (rough, each is its own sitting, not a unit):**
- Render Blueprint deploy: 10–15 minutes (walkthrough above) — mostly waiting on the first
  build, not decisions.
- Vapi key restriction: 15 minutes, dashboard only (steps above).
- Re-run `create-assistant` once deployed, so the live assistant picks up `recordingEnabled:
  false` and points at the real URL instead of a dead tunnel: 5 minutes.
- Optional Starter+disk upgrade, if cold starts/cap resets bother you: 10 minutes, +$7.25/mo.
- Hard caps, recording claim, SQLite decision, demo-sheet exposure, the Render Blueprint
  itself: **done**, no remaining effort — see table above.

**Blocks:** A live, public demo link. Nothing else.

---

## Critical path, in order

```
0. Commit & push ── DONE ─────────────────────┐
                                                ├─→ visible on GitHub
1. Label 9 calls → run-eval (free, any time)   │
2. Live verification call (spoken-form check)  ├─→ publishable portfolio piece
3. README (design-first, results table         │   (steps 1–3, in any order,
   after) + video + write-up                   ┘   independent of each other)

── optional, additive, start only if you want a live link ──
4a. Recording-claim fix, hard caps, demo-sheet exposure fix, Render Blueprint ── DONE
4b. Render Blueprint deploy (10-15 min) + Vapi key restriction (15 min, dashboard)
4c. Re-run create-assistant pointed at the deployed URL (5 min)
4d. End-to-end check on the live URL: replay, caps, mic pre-check
    optional: Starter + disk upgrade if cold starts/cap resets bother you ($7.25/mo)
```

**You can ship without:** everything in section 4. Also without item 2, if you're willing to
label Pass 3 "built, pending verification" in the write-up rather than claim it works — not
recommended given it's the one piece that's never been run, but it's not a hard blocker on
publishing the rest.
