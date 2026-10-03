"""FastAPI server Vapi calls during a live call: tool-call requests and other server events.

Run locally with `uvicorn collections_agent.webhooks.server:app` and expose it via a tunnel
(ngrok/cloudflared) so Vapi can reach it — see the Step 2 setup notes in the execution plan.

Vapi's tool-call webhook shape (docs.vapi.ai/tools/custom-tools, verify against your live
workspace — field naming has some drift across Vapi doc versions):
    request:  {"message": {"type": "tool-calls", "toolCallList": [{"id","name","arguments"}]}}
    response: {"results": [{"toolCallId": "...", "result": ...}]}
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from collections_agent.webhooks.auth import verify_vapi_secret
from collections_agent.webhooks.demo_live import build_live_call_config, handle_end_of_call, live_stream, record_tool_call
from collections_agent.webhooks.demo_replay import SCENARIOS, replay_stream
from collections_agent.webhooks.handlers import UnknownToolError, dispatch

logger = logging.getLogger("collections_agent.webhooks")

app = FastAPI(title="collections-agent webhooks")

TEST_CALL_PAGE = Path("fixtures/test_call.html")
DEMO_PAGE = Path(__file__).resolve().parent / "static" / "demo.html"

# Deliberately separate from handlers.TOOL_CALL_LOG_PATH: that file only gets an entry when a
# tool call is actually dispatched, so a request that arrives with an empty/malformed
# toolCallList leaves no trace anywhere — exactly the blind spot that made three real 200s
# during a live call impossible to explain afterward (see docs/FAILURES.md). This logs every
# request verbatim, before any parsing, so that can't happen again.
WEBHOOK_REQUEST_LOG_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "webhook_requests.jsonl"


def _log_raw_request(endpoint: str, body: dict[str, Any]) -> None:
    WEBHOOK_REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"logged_at": datetime.now(UTC).isoformat(), "endpoint": endpoint, "body": body}
    with WEBHOOK_REQUEST_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


@app.post("/vapi/tool-calls", dependencies=[Depends(verify_vapi_secret)])
async def vapi_tool_calls(request: Request) -> dict[str, Any]:
    body = await request.json()
    _log_raw_request("/vapi/tool-calls", body)
    message = body.get("message", {})
    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []

    results = []
    for call in tool_calls:
        call_id = call.get("id")
        name = call.get("name") or call.get("function", {}).get("name")
        arguments = _parse_arguments(call.get("arguments") or call.get("function", {}).get("arguments"))
        try:
            result = dispatch(name, arguments)
        except UnknownToolError:
            logger.warning("unknown tool call: %s", name)
            result = {"error": f"unknown tool: {name}"}
        except Exception:
            logger.exception("tool call handler failed: %s", name)
            result = {"error": "internal error handling tool call"}
        else:
            # Demo console Pass 3: mirror a real dispatch onto the live SSE channel, if a demo
            # page happens to be listening. Never lets a demo-console problem affect the actual
            # webhook response Vapi is waiting on.
            try:
                record_tool_call(name, arguments, result)
            except Exception:
                logger.exception("failed to push live demo tool-call event")
        results.append({"toolCallId": call_id, "result": result})

    return {"results": results}


@app.post("/vapi/events", dependencies=[Depends(verify_vapi_secret)])
async def vapi_events(request: Request) -> dict[str, str]:
    body = await request.json()
    message = body.get("message", {})
    logger.info("vapi event: %s", message.get("type"))
    if message.get("type") == "end-of-call-report":
        # Runs the real post-call pipeline and streams it to the demo console in the
        # background — must not delay this response, which Vapi is waiting on.
        asyncio.create_task(_run_end_of_call_safely(message))
    return {"status": "ok"}


async def _run_end_of_call_safely(message: dict[str, Any]) -> None:
    try:
        await handle_end_of_call(message)
    except Exception:
        logger.exception("demo console: failed to process end-of-call-report")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/test-call")
async def test_call_page() -> FileResponse:
    if not TEST_CALL_PAGE.exists():
        raise HTTPException(status_code=404, detail="Run `test-call --dial` first to generate this page.")
    return FileResponse(TEST_CALL_PAGE, media_type="text/html")


@app.get("/demo")
async def demo_page() -> FileResponse:
    return FileResponse(DEMO_PAGE, media_type="text/html")


@app.get("/demo/replay/{scenario}")
async def demo_replay_endpoint(scenario: str) -> StreamingResponse:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown replay scenario: {scenario}")
    return StreamingResponse(
        replay_stream(scenario),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/demo/live/config")
async def demo_live_config() -> dict[str, Any]:
    return build_live_call_config()


@app.get("/demo/live")
async def demo_live_endpoint() -> StreamingResponse:
    return StreamingResponse(
        live_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
