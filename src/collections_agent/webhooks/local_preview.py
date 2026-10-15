"""Loopback-only local UI using the hosted demo's sessions, callbacks and caps.

Run: uv run uvicorn collections_agent.webhooks.local_preview:app --host 127.0.0.1 --port 8766
This serves the working-copy HTML. Only public demo endpoints are forwarded; no local
API keys are loaded or sent. Calls and replay analysis still consume hosted project credit.
"""

from __future__ import annotations

import re
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

HOSTED = "https://collections-voice-agent-demo.onrender.com"
DEMO_PAGE = Path(__file__).parent / "static" / "demo.html"
VISITOR_COOKIE = "demo_visitor_id"
app = FastAPI(title="Collections local preview", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Local preview requires a loopback host"}, status_code=403)
    return await call_next(request)


@app.get("/demo")
async def demo():
    return FileResponse(DEMO_PAGE, headers={"Cache-Control": "no-store"})


def allowed(method: str, path: str) -> bool:
    if method == "GET":
        return path in {
            "/demo/live/status",
            "/demo/evidence/latency",
            "/demo/assets/vapi-web-2.7.1.js",
            "/demo/assets/system-prompt.txt",
            "/demo/assets/writeback-example.xlsx",
            "/demo/replay/clean",
            "/demo/replay/flagged",
        } or bool(re.fullmatch(r"/demo/live/[A-Za-z0-9_-]{20,64}", path))
    return method == "POST" and (
        path == "/demo/live/config"
        or bool(re.fullmatch(r"/demo/live/[A-Za-z0-9_-]{20,64}/(connected|cancel|ended)", path))
    )


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def hosted_demo(request: Request, path: str):
    path = "/" + path
    if not allowed(request.method, path):
        raise HTTPException(404)
    # Enforce the local browser boundary before forwarding with the hosted origin.
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != f"{request.url.scheme}://{request.headers.get('host')}"
    ):
        raise HTTPException(403, "Cross-origin demo requests are not allowed")
    headers = {"origin": HOSTED, "accept-encoding": "identity"}
    for key in ("accept", "content-type", "last-event-id"):
        if key in request.headers:
            headers[key] = request.headers[key]
    visitor = request.cookies.get(VISITOR_COOKIE)
    if visitor:
        try:
            headers["cookie"] = f"{VISITOR_COOKIE}={uuid.UUID(visitor)}"
        except ValueError:
            raise HTTPException(400, "Invalid visitor cookie") from None
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=180), follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(
                request.method, HOSTED + path, headers=headers, content=await request.body()
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "Hosted demo could not be reached") from None

    async def close():
        await upstream.aclose()
        await client.aclose()

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await close()

    response = StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers={
            key: upstream.headers[key]
            for key in ("content-type", "cache-control", "x-accel-buffering", "content-encoding")
            if key in upstream.headers
        },
        background=BackgroundTask(close),
    )
    # Reissue just the demo visitor cookie for this loopback origin. Never forward
    # unrelated cookies, auth headers, or the remote cookie's Domain/Secure attributes.
    for value in upstream.headers.get_list("set-cookie"):
        cookies = SimpleCookie()
        cookies.load(value)
        if VISITOR_COOKIE in cookies:
            item = cookies[VISITOR_COOKIE]
            response.set_cookie(
                VISITOR_COOKIE,
                str(uuid.UUID(item.value)),
                max_age=int(item["max-age"]) if item["max-age"] else None,
                httponly=True,
                samesite="lax",
                secure=urlsplit(str(request.url)).scheme == "https",
            )
    return response
