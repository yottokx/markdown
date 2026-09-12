from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from desktop_llm.api import ChatCompletionStream
from desktop_llm.models import ApiEndpoint, ModelProfile

from .directives import Directive, DirectiveKind


class CompletionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    document: str
    revision: int
    directives: tuple[Directive, ...]


@dataclass(frozen=True, slots=True)
class CompletionValue:
    text: str = ""
    items: tuple[str, ...] = ()


class CompletionProvider(ABC):
    @abstractmethod
    def complete(self, request: CompletionRequest) -> dict[str, CompletionValue]:
        raise NotImplementedError

    @abstractmethod
    def cancel(self) -> None:
        raise NotImplementedError


class MockCompletionProvider(CompletionProvider):
    def __init__(self, delay_seconds: float = 0.7) -> None:
        self.delay_seconds = delay_seconds
        self._cancelled = threading.Event()

    def complete(self, request: CompletionRequest) -> dict[str, CompletionValue]:
        if self._cancelled.wait(self.delay_seconds):
            raise CompletionError("補完をキャンセルしました。")
        values: dict[str, CompletionValue] = {}
        for directive in request.directives:
            if directive.kind == DirectiveKind.INLINE:
                text = _mock_inline(directive.hint)
                values[directive.id] = CompletionValue(text=text)
            elif directive.kind == DirectiveKind.LINE_END:
                values[directive.id] = CompletionValue(text="を自然に補完します。")
            elif directive.kind == DirectiveKind.PARAGRAPH:
                values[directive.id] = CompletionValue(
                    text=f"{directive.hint}際は、目的と前提条件を明確にし、必要な情報を簡潔に整理します。"
                )
            elif directive.kind == DirectiveKind.LIST_SINGLE:
                values[directive.id] = CompletionValue(text=f"{directive.hint}を明確にする")
            else:
                count = _requested_count(directive.hint)
                values[directive.id] = CompletionValue(
                    items=tuple(f"{directive.hint.rstrip('。')}（{index}）" for index in range(1, count + 1))
                )
        return values

    def cancel(self) -> None:
        self._cancelled.set()


class OpenAIProfileCompletionProvider(CompletionProvider):
    """Complete directives using an existing Desktop LLM model profile."""

    def __init__(
        self,
        endpoint: ApiEndpoint,
        profile: ModelProfile,
        api_key: str,
        reasoning_effort: str | None,
    ) -> None:
        self.endpoint = endpoint
        self.profile = profile
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self._active_stream: ChatCompletionStream | None = None
        self._lock = threading.Lock()

    def complete(self, request: CompletionRequest) -> dict[str, CompletionValue]:
        messages: list[dict[str, str]] = []
        if self.profile.system_prompt:
            messages.append({"role": "system", "content": self.profile.system_prompt})
        messages.extend(
            [
                {"role": "system", "content": _COMPLETION_SYSTEM_PROMPT},
                {"role": "user", "content": _request_payload(request)},
            ]
        )
        stream = ChatCompletionStream(
            self.endpoint,
            self.profile,
            self.api_key,
            messages,
            self.reasoning_effort,
        )
        with self._lock:
            self._active_stream = stream
        content: list[str] = []
        try:
            for event in stream.events():
                if event.kind == "content_delta":
                    content.append(event.text)
                elif event.kind == "error":
                    raise CompletionError(event.text)
                elif event.kind == "completed" and bool((event.data or {}).get("cancelled")):
                    raise CompletionError("補完をキャンセルしました。")
        finally:
            with self._lock:
                self._active_stream = None
        return _parse_response("".join(content), request)

    def cancel(self) -> None:
        with self._lock:
            stream = self._active_stream
        if stream is not None:
            stream.cancel()


_COMPLETION_SYSTEM_PROMPT = """\
You fill explicit placeholders in a Markdown prompt document.
Return JSON only. Do not wrap it in Markdown fences.
The response schema is {"replacements":[{"id":"...","text":"..."}]}.
For kind=list_multi use {"id":"...","items":["item one","item two"]}.
For inline, line_end and list_single, text must contain no newline.
For line_end, return only the continuation to insert at the cursor. Do not repeat existing text and do not add a newline.
For paragraph, text should normally be one paragraph.
For list values, return item bodies without Markdown list markers.
Return exactly one replacement for every requested id and no additional ids.
Use the full document as context, but replace only the requested directives.
"""


def _request_payload(request: CompletionRequest) -> str:
    targets: list[dict[str, Any]] = []
    for directive in request.directives:
        target: dict[str, Any] = {
            "id": directive.id,
            "kind": directive.kind.value,
            "hint": directive.hint,
        }
        if directive.kind == DirectiveKind.LINE_END:
            target["cursor_position"] = directive.start
        if directive.kind == DirectiveKind.LIST_MULTI:
            target["default_count"] = 3
            target["maximum_count"] = 10
        targets.append(target)
    return json.dumps(
        {"document": request.document, "targets": targets},
        ensure_ascii=False,
    )


def _parse_response(
    raw: str, request: CompletionRequest
) -> dict[str, CompletionValue]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise CompletionError("補完応答がJSONではありません。") from exc
    rows = payload.get("replacements") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CompletionError("補完応答にreplacementsがありません。")

    expected = {directive.id: directive for directive in request.directives}
    values: dict[str, CompletionValue] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise CompletionError("補完応答の項目形式が不正です。")
        directive_id = row["id"]
        directive = expected.get(directive_id)
        if directive is None or directive_id in values:
            raise CompletionError("補完応答に不明または重複したIDがあります。")
        if directive.kind == DirectiveKind.LIST_MULTI:
            items = row.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 10:
                raise CompletionError("複数リスト補完の件数が不正です。")
            normalized = tuple(_single_line(item) for item in items if isinstance(item, str))
            if len(normalized) != len(items) or any(not item for item in normalized):
                raise CompletionError("複数リスト補完に空または不正な項目があります。")
            values[directive_id] = CompletionValue(items=normalized)
        else:
            text = row.get("text")
            if not isinstance(text, str) or not text.strip():
                raise CompletionError("補完応答のテキストが空です。")
            normalized_text = text.strip()
            if directive.kind in {
                DirectiveKind.INLINE,
                DirectiveKind.LINE_END,
                DirectiveKind.LIST_SINGLE,
            }:
                normalized_text = _single_line(normalized_text)
            values[directive_id] = CompletionValue(text=normalized_text)
    if set(values) != set(expected):
        raise CompletionError("補完応答に不足している項目があります。")
    return values


def _single_line(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def _requested_count(hint: str) -> int:
    match = re.search(r"(\d+)\s*(?:つ|件|個|項目)", hint)
    if match is None:
        return 3
    return min(10, max(1, int(match.group(1))))


def _mock_inline(hint: str) -> str:
    known = {
        "人名": "山田太郎",
        "会社名": "株式会社サンプル",
        "日付": "2026年8月23日",
    }
    return known.get(hint, f"{hint}の補完例")
