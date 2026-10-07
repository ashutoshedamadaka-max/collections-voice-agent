# Live demo implementation plan

Status: proposed; live capability changes await discussion and approval. The current UI update
separates voice controls, Instructions, and Conversation. It does not complete the live-call
integration described below.

## Intended experience

A visitor speaks to Northgate's agent through their browser microphone, playing the customer
shown in Instructions. They can instead replay a saved conversation. Both paths show the
transcript, captured actions, specialist analysis, write decision, and costs. Customer and
invoice details remain visible in the middle panel, using the actual selected scenario.

## 1. Verify readiness and repair event delivery

- Verify the production public key, assistant ID, allowed origins, provider configuration,
  and webhook authentication without exposing private credentials in the browser.
- Route tool calls and end-of-call reports correctly. The assistant inspected during the
  audit sent both to `/vapi/tool-calls`, but post-call processing is in `/vapi/events`.
- Make availability distinguish configuration problems, visitor/daily caps, budget limits,
  and a session already in progress. The existing status check only evaluates usage caps.
- Preserve the three-minute duration cap and disabled audio recording.

## 2. Add live-call guidance to Instructions

This is a required part of implementing the actual live demo, not an optional polish task.
Replay guidance alone is insufficient for a first-time visitor.

Provide clearly labeled guidance for both **Talk to the agent** and **Replay a demo**. Keep
both discoverable before starting, including when live calling is temporarily capped. Explain
the active mode clearly; a replay must never imply that the visitor's microphone is being used.

Live-call instructions should cover:

1. **Your role:** "You're playing [customer name], [role] at [company]. The agent is calling
   about the overdue invoice shown below." Populate this from the current scenario.
2. **Before starting:** allow microphone access, complete the microphone test, and choose
   **Talk to the agent**. Headphones are optional and can help avoid speaker echo.
3. **During the conversation:** listen to the opening, answer as the customer in your own
   words, and speak naturally into the microphone. No memorized script is required.
4. **Things to try:** explain that payment is pending approval, offer a specific payment
   date and amount, dispute the invoice, request a callback, or ask about a discount.
   Present these as optional conversation ideas, not mandatory answers.
5. **Call controls:** explain mute, audio output, and End call once each control is actually
   implemented and verified. Do not describe a disabled control as functional.
6. **After the call:** the page moves to Call intelligence, where captured details are
   checked by specialists and a write decision appears. Explain that analysis can take a
   little longer than the spoken conversation.

Replay instructions should continue to explain that no microphone is needed, what to watch
for in the saved conversation, and how captured actions lead into analysis and a decision.

Keep essential role and speaking instructions visible during a live call. Preserve all
customer/invoice details, deferred-invoice notes, and historical-scenario context. Do not
put essential instructions inside a small nested scrolling area. On phones, show Instructions
before the call controls and transcript.

## 3. Complete the call lifecycle

- Use clearly labeled live and replay actions with explicit permission, connecting, active,
  ended, analyzing, and failure states.
- Wire microphone mute and audio controls to the supported SDK behavior.
- End audio promptly on hangup while keeping the result stream open for post-call analysis.
- Keep automatic scrolling to Call intelligence once per completed conversation, respecting
  reduced motion and leaving other documentation tabs undisturbed.
- Allow recovery from microphone denial, failed connection, disconnected audio, and missing
  analysis events without leaving the visitor stuck.

## 4. Protect session boundaries and usage limits

- Isolate each visitor's result stream. The current in-process global queue/history can mix
  data from overlapping calls; initially prevent simultaneous live sessions as well.
- Do not consume a visitor's allowance simply because a connection attempt failed.
- Persist usage accounting across service restarts; select the storage/hosting approach
  before making any paid infrastructure changes.
- Retain the existing public limits initially. Keep an operator testing allowance separate
  from public access if one is added.

## 5. Acceptance checks

- Complete a real spoken conversation with the user, then verify transcript, tool calls,
  specialist findings, write decision, and costs against that same call.
- Check microphone permission denial, mute/unmute, speaker behavior, interruptions, hangup,
  timeout, capped availability, and replay fallback.
- Confirm Instructions explains both modes and stays accurate before, during, and after
  live and replay sessions, including switching to a different saved customer.
- Verify Instructions remains readable at desktop, tablet, and phone widths, with no lost
  information or unreachable controls.
- Confirm a second visitor cannot receive another visitor's call events, and failed starts
  do not unnecessarily exhaust the demo allowance.

Actual ledger writes are a separate scope: the public demo currently displays a write
decision and row previews. Do not claim that enabling live voice also enables real ledger writes.
