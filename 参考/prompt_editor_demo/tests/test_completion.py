import json

import pytest

from desktop_llm.api import StreamEvent
from desktop_llm.models import ApiEndpoint, ModelProfile
from prompt_editor_demo.completion import (
    CompletionError,
    CompletionRequest,
    MockCompletionProvider,
    OpenAIProfileCompletionProvider,
    _parse_response,
)
from prompt_editor_demo.directives import parse_directives


def test_mock_provider_returns_typed_values() -> None:
    document = "私は ?人名 です。\n- ?注意点を3つ..."
    directives = tuple(parse_directives(document))
    request = CompletionRequest(document, 1, directives)

    values = MockCompletionProvider(delay_seconds=0).complete(request)

    assert values[directives[0].id].text == "山田太郎"
    assert len(values[directives[1].id].items) == 3


def test_profile_response_requires_every_target() -> None:
    document = "私は ?人名 です。\n- ?注意点を3つ..."
    directives = tuple(parse_directives(document))
    request = CompletionRequest(document, 1, directives)
    raw = json.dumps(
        {"replacements": [{"id": directives[0].id, "text": "山田太郎"}]},
        ensure_ascii=False,
    )

    with pytest.raises(CompletionError, match="不足"):
        _parse_response(raw, request)


def test_profile_response_accepts_json_fence_and_list_items() -> None:
    document = "- ?注意点を3つ..."
    directive = parse_directives(document)[0]
    request = CompletionRequest(document, 1, (directive,))
    raw = "```json\n" + json.dumps(
        {
            "replacements": [
                {"id": directive.id, "items": ["項目A", "項目B", "項目C"]}
            ]
        },
        ensure_ascii=False,
    ) + "\n```"

    values = _parse_response(raw, request)

    assert values[directive.id].items == ("項目A", "項目B", "項目C")


def test_profile_provider_uses_existing_profile_and_parses_stream(monkeypatch) -> None:
    document = "私は ?人名 です。"
    directive = parse_directives(document)[0]
    request = CompletionRequest(document, 1, (directive,))
    captured = {}

    class FakeStream:
        def __init__(self, endpoint, profile, api_key, messages, reasoning_effort) -> None:
            captured.update(
                endpoint=endpoint,
                profile=profile,
                api_key=api_key,
                messages=messages,
                reasoning_effort=reasoning_effort,
            )

        def events(self):
            payload = json.dumps(
                {"replacements": [{"id": directive.id, "text": "山田太郎"}]},
                ensure_ascii=False,
            )
            yield StreamEvent("content_delta", payload)
            yield StreamEvent("completed", data={"cancelled": False})

        def cancel(self) -> None:
            return None

    monkeypatch.setattr("prompt_editor_demo.completion.ChatCompletionStream", FakeStream)
    endpoint = ApiEndpoint("e", "Local", "http://localhost:8080/v1")
    profile = ModelProfile(
        "p", "Prompt", "e", "model", ("off", "on"), 0.2, "既存のシステムプロンプト"
    )
    provider = OpenAIProfileCompletionProvider(endpoint, profile, "secret", "off")

    values = provider.complete(request)

    assert values[directive.id].text == "山田太郎"
    assert captured["endpoint"] == endpoint
    assert captured["profile"] == profile
    assert captured["api_key"] == "secret"
    assert captured["reasoning_effort"] == "off"
    assert captured["messages"][0]["content"] == "既存のシステムプロンプト"
