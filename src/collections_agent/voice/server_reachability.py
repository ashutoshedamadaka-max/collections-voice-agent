"""Pre-dial guard: confirms the assistant's configured webhook server actually responds
before a call starts. An unreachable server doesn't fail loudly — Vapi just never gets a
tool-call response back, which the caller experiences as the agent going silent (see
docs/FAILURES.md for the incident this was written for). Checking this before every `--dial`
catches a stale/dead tunnel before it wastes Vapi minutes and looks like an agent bug.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def server_url_from_assistant(assistant: dict[str, Any]) -> str | None:
    """The configured webhook base URL, preferring the current `server.url` field over the
    legacy `serverUrl` string — Vapi's current API doesn't define `serverUrl` at all, but an
    assistant saved with it before that was noticed can still echo it back unused.
    """
    server = assistant.get("server") or {}
    return server.get("url") or assistant.get("serverUrl") or None


def check_server_reachable(server_url: str, timeout: float = 5.0) -> str | None:
    """None if `server_url`'s host answers `/health` successfully; otherwise a short,
    human-readable reason it doesn't (used directly in the CLI's refusal message).
    """
    parts = urlsplit(server_url)
    health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
    try:
        resp = httpx.get(health_url, timeout=timeout)
    except httpx.RequestError as e:
        return f"{health_url} did not respond ({e.__class__.__name__}: {e})"
    if resp.status_code >= 400:
        return f"{health_url} returned HTTP {resp.status_code}"
    return None
