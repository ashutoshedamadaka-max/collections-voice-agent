"""Verifies inbound Vapi webhook requests carry the shared server secret.

Vapi sends the secret configured in the dashboard (attached to the server URL as a
credential) back on every request as the `X-Vapi-Secret` header. Compared in constant time
to avoid a timing side-channel.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from collections_agent.config import get_settings

VAPI_SECRET_HEADER = "X-Vapi-Secret"


def verify_vapi_secret(request: Request) -> None:
    settings = get_settings()
    expected = settings.vapi_server_secret
    if not expected:
        # Misconfiguration, not a client error — fail loudly rather than accept anything.
        raise HTTPException(status_code=500, detail="VAPI_SERVER_SECRET is not configured")

    provided = request.headers.get(VAPI_SECRET_HEADER, "")
    if not provided:
        raise HTTPException(status_code=401, detail="missing Vapi secret header")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid Vapi secret")
