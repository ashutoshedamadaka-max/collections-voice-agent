"""Thin wrapper isolating the exact OpenAI structured-output call shape behind one function,
so a future SDK signature change (this project has already hit that class of bug once with
Vapi — see docs/FAILURES.md) touches one file, and every specialist is testable by monkeypatching
`extract_structured` instead of hitting a real API. Structured outputs via `response_format:
json_schema` has been stable OpenAI API surface since 2024; verify against your installed
`openai` package version before spending real money if this ever throws.
"""

from __future__ import annotations

from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

SPECIALIST_MODEL = "gpt-4o-mini"  # cheap, ~$1 budget for Step 4 — independent of the in-call
# backend model (voice/assistant_config.py's BACKEND_MODEL), which is a separate experiment


def _make_strict(schema: dict[str, Any]) -> None:
    """OpenAI's strict structured-output mode requires every property to be listed in
    `required` (a field that's optional in Python must still be present-but-nullable in the
    schema, not absent from `required`) and every object to set `additionalProperties: false`.
    `BaseModel.model_json_schema()` does neither by default — verified directly against this
    project's own domain models (`OutcomeExtraction.reason_code` is exactly this shape) — so
    every specialist call would fail schema validation on first real use without this.
    """
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())
        for prop_schema in schema["properties"].values():
            _make_strict(prop_schema)
    for branch in schema.get("anyOf", []):
        _make_strict(branch)
    if "items" in schema:
        _make_strict(schema["items"])


def _strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    raw = schema.model_json_schema()
    for def_schema in raw.get("$defs", {}).values():
        _make_strict(def_schema)
    _make_strict(raw)
    return raw


def extract_structured(
    system_prompt: str,
    user_content: str,
    schema: type[ModelT],
    api_key: str,
    model: str = SPECIALIST_MODEL,
    usage_sink: list[Any] | None = None,
) -> ModelT:
    """Calls OpenAI with `schema`'s JSON schema as a structured-output constraint and parses
    the response into an instance of `schema`. Raises whatever the SDK raises on failure —
    callers decide how to handle a specialist that couldn't run (see pipeline.py).

    `usage_sink`, when given, gets the response's `usage` object appended — the demo replay
    endpoint uses this to report a real per-call pipeline cost instead of an estimate. Optional
    and unused by every existing caller, so this changes nothing for them.
    """
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": _strict_json_schema(schema),
                "strict": True,
            },
        },
    )
    if usage_sink is not None and response.usage is not None:
        usage_sink.append(response.usage)
    content = response.choices[0].message.content
    return schema.model_validate_json(content)
