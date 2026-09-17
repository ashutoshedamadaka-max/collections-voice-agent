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


def get_settings() -> Settings:
    return Settings()
