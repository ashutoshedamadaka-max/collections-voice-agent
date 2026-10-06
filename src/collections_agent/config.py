"""Typed settings loaded from environment variables / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_sheet_id: str = ""
    google_service_account_json: str = ""

    openai_api_key: str = ""

    vapi_api_key: str = ""
    vapi_server_secret: str = ""
    vapi_assistant_id: str = ""
    # Public key (Vapi Dashboard -> API Keys), safe to ship to the browser — used by the
    # Web SDK in fixtures/test_call.html (see `test-call --dial`). Never use vapi_api_key
    # (private) client-side.
    vapi_public_key: str = ""
    # Set this to bill the in-call model to YOUR OpenAI balance instead of Vapi-hosted
    # credits: Vapi Dashboard -> Provider Keys -> add your OpenAI key -> copy the resulting
    # credential id here. Verify the exact field name against your dashboard/API version —
    # Vapi's BYOK wiring has moved around across API versions.
    vapi_openai_credential_id: str = ""

    company_name: str = "Acme Supplies"

    confidence_threshold: float = 0.75

    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000

    # Demo console caps (webhooks/demo_caps.py) — all server-side, all configurable, because
    # the live demo spends real Vapi credit against a small, finite balance. See docs/
    # SHIP_PLAN.md for the budget this was tuned against.
    demo_budget_usd: float = 4.87
    demo_spend_floor_usd: float = 1.00
    demo_daily_ceiling: int = 3
    demo_visitor_window_hours: int = 24
    demo_calls_per_visitor_window: int = 1
    demo_state_db_path: str = "fixtures/demo_state.db"


def get_settings() -> Settings:
    return Settings()
