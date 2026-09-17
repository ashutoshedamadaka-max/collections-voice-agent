"""Tests the structured-output parsing in isolation, without a real OpenAI call — the OpenAI
client class is monkeypatched with a fake that returns a canned chat-completion response
shaped like the real SDK's, so a future SDK signature drift (see openai_client.py's docstring)
would show up here first.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from collections_agent.postcall import openai_client as openai_client_module
from collections_agent.postcall.openai_client import extract_structured


class _Sample(BaseModel):
    name: str
    score: float


class _WithOptionalField(BaseModel):
    name: str
    nickname: str | None = None


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_call: dict | None = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeOpenAI:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)
        self.api_key_received: str | None = None

    def __call__(self, api_key: str):
        self.api_key_received = api_key
        return self


@pytest.fixture
def fake_openai(monkeypatch):
    fake = _FakeOpenAI(json.dumps({"name": "alpha", "score": 0.9}))
    monkeypatch.setattr(openai_client_module, "OpenAI", fake)
    return fake


def test_extract_structured_parses_response_into_schema(fake_openai):
    result = extract_structured("system", "user", _Sample, api_key="key-123")
    assert result == _Sample(name="alpha", score=0.9)


def test_extract_structured_passes_api_key(fake_openai):
    extract_structured("system", "user", _Sample, api_key="key-123")
    assert fake_openai.api_key_received == "key-123"


def test_extract_structured_sends_system_and_user_messages(fake_openai):
    extract_structured("sys prompt", "user content", _Sample, api_key="key-123")
    messages = fake_openai.chat.completions.last_call["messages"]
    assert messages[0] == {"role": "system", "content": "sys prompt"}
    assert messages[1] == {"role": "user", "content": "user content"}


def test_extract_structured_requests_json_schema_for_the_given_model(fake_openai):
    extract_structured("system", "user", _Sample, api_key="key-123")
    response_format = fake_openai.chat.completions.last_call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "_Sample"


def test_extract_structured_makes_optional_fields_required_but_nullable(monkeypatch):
    """OpenAI's strict mode rejects a schema where an optional Python field is missing from
    `required` — pydantic's default model_json_schema() does exactly that, so this is the bug
    that would make every real specialist call fail on first use without the fix."""
    fake = _FakeOpenAI(json.dumps({"name": "alpha", "nickname": None}))
    monkeypatch.setattr(openai_client_module, "OpenAI", fake)

    extract_structured("system", "user", _WithOptionalField, api_key="key-123")

    sent_schema = fake.chat.completions.last_call["response_format"]["json_schema"]["schema"]
    assert set(sent_schema["required"]) == {"name", "nickname"}
    assert sent_schema["additionalProperties"] is False
