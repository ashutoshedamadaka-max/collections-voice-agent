"""Renders fixtures/test_call.html — a static, bundler-free page that drives a Vapi web
call via the CDN script tag (https://cdn.jsdelivr.net/gh/VapiAI/html-script-tag), so
`test-call --dial` can be tested in a browser without npm/a build step or a phone number.

Vapi's REST `POST /call` no longer accepts a phone-number-less "web call" (it now requires
`type: outboundPhoneCall` or `inboundPhoneCall` either way) — this is why the call must be
started client-side with the Web SDK's public key instead of server-side with the private
`vapi_api_key`. Verified against the SDK's actual source
(https://github.com/VapiAI/client-sdk-web/blob/main/vapi.ts) rather than docs, since Vapi's
API has moved fields/behavior around across versions before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _safe_script_json(data: dict[str, Any]) -> str:
    """JSON for embedding inside a `<script type="application/json">` block.

    Escapes "</" so a value that happens to contain "</script>" (e.g. inside the rendered
    system prompt) can't prematurely close the tag; "\\/" is a valid JSON escape for "/", so
    `JSON.parse` recovers the original string unchanged.
    """
    return json.dumps(data).replace("</", "<\\/")


def render_test_call_page(
    account_label: str,
    system_prompt: str,
    public_key: str,
    assistant_id: str,
    assistant_overrides: dict[str, Any],
) -> str:
    config_json = _safe_script_json(
        {
            "publicKey": public_key,
            "assistantId": assistant_id,
            "assistantOverrides": assistant_overrides,
        }
    )
    template = _env.get_template("test_call.html.j2")
    return template.render(
        account_label=account_label,
        assistant_id=assistant_id,
        system_prompt=system_prompt,
        config_json=config_json,
    )
