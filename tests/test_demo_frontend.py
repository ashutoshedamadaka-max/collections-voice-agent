"""Execute the real startup functions with DOM IDs from the page and fake voice services."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "src/collections_agent/webhooks/static/demo.html"


def test_literal_dom_references_exist():
    html = PAGE.read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    refs = set(re.findall(r'getElementById\("([^"]+)"\)', html))
    assert not refs - ids


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for frontend runtime regression")
@pytest.mark.parametrize("scenario", ["connect", "mic_denied", "reset_error", "replay"])
def test_startup_runtime(scenario):
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m =>
  [m[1], {style:{}, className:'', textContent:'', disabled:false}]));
const calls = {media:0, fetch:[], actions:[], streams:[], errors:[]};
const noop = () => {};
const ctx = {
  document: {getElementById: id => elements[id] || null},
  window: {Vapi:function(){}},
  navigator:{mediaDevices:{getUserMedia:async () => {
    calls.media++;
    if(scenario === 'mic_denied') throw new Error('Microphone permission denied');
    return {getAudioTracks:()=>[{readyState:'live'}],getTracks:()=>[{stop:noop}]};
  }}},
  livePhase:'idle', liveAttempt:0, currentScenario:null, liveSessionId:null,
  liveCallId:null, liveTurns:[], endReported:false, liveCost:{},
  callStartWallClock:0, tickTimer:null, eventSource:null,
  setText:(id,text)=>{elements[id].textContent=text;},
  unsettleLayout:noop, resetCaptured:()=>{
    if(scenario === 'reset_error') throw new Error('Render failed');
  },
  renderSpecialistsEmpty:noop, renderDecisionEmpty:noop, resetTranscript:noop,
  renderPersonaBriefing:noop, renderCostBar:noop, stopClock:noop, tickClock:noop,
  setInterval:()=>1, setTimeout:()=>2, clearTimeout:noop,
  setLiveControls:noop, openLiveEventSource:noop,
  fetch:async url=>{
    calls.fetch.push(url);
    return {ok:true,json:async()=>({available:true,sessionId:'session',account:{},
      publicKey:'public',assistantId:'assistant',assistantOverrides:{}})};
  },
  initVapi:()=>({start:async()=>({id:'fake-call'}),stop:async()=>{}}),
  liveAction:async (...args)=>{calls.actions.push(args);}, reportEndedCall:noop,
  liveFailed:async message=>{calls.errors.push(message);ctx.livePhase='failed';},
  EventSource:class {constructor(url){calls.streams.push(url);}addEventListener(){}},
  applyMeta:noop, handleTurn:noop, handleToolCall:noop, handleCallEnded:noop,
  resolveSpecialistChip:noop, handleFinal:noop, handleReplayFailure:noop
};
vm.createContext(ctx);
function section(start, end) {
  const from = html.indexOf(start);
  assert(from !== -1, 'Function missing: '+start);
  const to = html.indexOf(end,from+start.length);
  assert(to !== -1,'Function boundary missing');
  return html.slice(from,to);
}
vm.runInContext(section('  function resetUiForCall(mode)', '  function applyAccountFields'),ctx);
vm.runInContext(section('  async function startLiveCall()',
  '  document.getElementById("muteMicBtn").addEventListener'),ctx);
vm.runInContext(section('  function startReplay(scenarioKey)', '  function handleReplayFailure'),ctx);
(async()=>{
  if(scenario === 'replay') {
    ctx.startReplay('clean');
    assert.deepEqual(calls.streams,['/demo/replay/clean']);
    assert.equal(calls.media,0);
    assert.equal(calls.fetch.length,0);
  } else {
    await ctx.startLiveCall();
    if(scenario === 'connect') {
      assert.equal(calls.media,1);
      assert.deepEqual(calls.fetch,['/demo/live/config']);
      assert.equal(calls.actions[0][1],'connected');
      assert.equal(ctx.liveCallId,'fake-call');
      assert.deepEqual(calls.errors,[]);
    } else {
      assert.equal(calls.fetch.length,0);
      assert.equal(ctx.livePhase,'failed');
      const expected = scenario === 'mic_denied' ? 'Microphone permission denied' : 'Render failed';
      assert.equal(calls.errors[0],expected);
    }
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [shutil.which("node"), "-", str(PAGE), scenario],
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
