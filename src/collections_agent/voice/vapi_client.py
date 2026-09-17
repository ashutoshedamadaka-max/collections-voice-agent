"""Thin httpx wrapper over the handful of Vapi REST endpoints this project needs.

No official Vapi Python SDK dependency — this keeps the surface small and every call
trivially mockable in tests (via `respx`).
"""

from __future__ import annotations

from typing import Any

import httpx

VAPI_BASE_URL = "https://api.vapi.ai"


class VapiClient:
    def __init__(self, api_key: str, base_url: str = VAPI_BASE_URL, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def list_assistants(self) -> list[dict[str, Any]]:
        resp = self._client.get("/assistant")
        resp.raise_for_status()
        return resp.json()

    def create_assistant(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/assistant", json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_assistant(self, assistant_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/assistant/{assistant_id}")
        resp.raise_for_status()
        return resp.json()

    def update_assistant(self, assistant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(f"/assistant/{assistant_id}", json=payload)
        resp.raise_for_status()
        return resp.json()

    def start_call(
        self,
        assistant_id: str,
        phone_number_id: str,
        customer_number: str,
        assistant_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assistantId": assistant_id,
            "phoneNumberId": phone_number_id,
            "customer": {"number": customer_number},
        }
        if assistant_overrides:
            payload["assistantOverrides"] = assistant_overrides
        resp = self._client.post("/call", json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_call(self, call_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/call/{call_id}")
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
